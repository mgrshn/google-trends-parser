"""
Worker process: pulls jobs from Redis, scrapes Google Trends, saves to PostgreSQL.

Usage:
    python worker.py                    # 1 worker, 10 concurrent tasks
    python worker.py --concurrency 20   # 20 concurrent tasks
    WORKER_ID=2 python worker.py        # multiple workers on same machine
"""
import argparse
import asyncio
import datetime
import logging
import os
import random
import signal

from curl_cffi.requests import AsyncSession
from app.db import Database, DSN
from app.proxy_pool import ProxyPool
from app.job_queue import Job, JobQueue
from app.scraper import GoogleTrendsScraper, ScrapePool, IMPERSONATE

_worker_id = os.getenv("WORKER_ID", "0")

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "info").upper(),
    format=f"%(asctime)s [W{_worker_id}] %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

MAX_ATTEMPTS = 3


async def get_exit_ip(proxy_url: str | None) -> str:
    try:
        async with AsyncSession(
            impersonate=IMPERSONATE,
            proxies={"https": proxy_url, "http": proxy_url} if proxy_url else None,
            timeout=6,
        ) as s:
            r = await s.get("https://ifconfig.me/ip")
            return r.text.strip()
    except Exception:
        return "?"


def _points_to_rows(points) -> list[dict]:
    rows = []
    for p in points:
        if not p.has_data:
            continue
        ts = datetime.datetime.fromtimestamp(p.timestamp, tz=datetime.timezone.utc)
        rows.append({"ts": ts, "value": p.value})
    return rows


def _compare_points_to_json(points: list[dict]) -> list[dict]:
    return [
        {
            "ts": datetime.datetime.fromtimestamp(p["timestamp"], tz=datetime.timezone.utc).isoformat(),
            "values": p["values"],
        }
        for p in points
    ]


async def _pause_all(event: asyncio.Event, seconds: float):
    event.clear()
    log.info("PAUSE %.1fs (429 burst detected)", seconds)
    await asyncio.sleep(seconds)
    event.set()
    log.info("RESUME after pause")


async def process_job(job: Job, proxy_pool: ProxyPool, scrape_pool: ScrapePool, db: Database, queue: JobQueue, sem: asyncio.Semaphore, pause: asyncio.Event, ban_429: float = 120.0, sleep_min: float = 0.0, sleep_max: float = 0.0, log_ip: bool = False, pause_on_429: float = 0.0):
    async with sem:
        await pause.wait()
        try:
            lease = await proxy_pool.acquire(timeout=180.0)
        except TimeoutError:
            log.error("No proxy available for %s — re-queuing", job.keyword)
            await queue.push([job])
            return
        try:
            ip = await get_exit_ip(lease.url) if log_ip else ""
            scraper, req_n = await scrape_pool.get(lease.url)
            if job.kind == "compare":
                keywords = job.keyword.split(",")
                result = await scraper.fetch_compare(keywords, job.geo, job.timeframe, job.category, job.gprop)
                rows = _compare_points_to_json(result.points)
                await db.save_compare(keywords, job.geo, job.timeframe, rows, job.category, job.gprop)
                n_items = len(rows)
            elif job.kind == "regions":
                data = await scraper.fetch_regions(job.keyword, job.geo, job.timeframe, job.category, job.gprop)
                await db.save_widget("regions", job.keyword, job.geo, job.timeframe, data, job.category, job.gprop)
                n_items = len(data)
            elif job.kind == "related":
                data = await scraper.fetch_related(job.keyword, job.geo, job.timeframe, job.category, job.gprop)
                await db.save_widget("related", job.keyword, job.geo, job.timeframe, data, job.category, job.gprop)
                n_items = len(data["top"]) + len(data["rising"])
            elif job.kind == "trending":
                hours = int(job.timeframe) if job.timeframe.isdigit() else 24
                data = await scraper.fetch_trending(job.geo, hours)
                await db.save_trending(job.geo, data)
                n_items = len(data)
            else:
                result = await scraper.fetch(job.keyword, job.geo, job.timeframe, job.category, job.gprop)
                rows = _points_to_rows(result.points)
                await db.save_points(job.keyword, job.geo, job.timeframe, rows, job.category, job.gprop)
                n_items = len(rows)
            kb = scraper._fetch_bytes / 1024
            ip_tag = f"  ip={ip}" if ip else ""
            log.info("OK  %-30s kind=%-8s geo=%-5s items=%-3d %5.1fkb req#%d%s",
                     job.keyword, job.kind, job.geo, n_items, kb, req_n, ip_tag)
            proxy_pool.report_success(lease.proxy)
            await queue.clear_inflight(job)
            if sleep_max > 0:
                await asyncio.sleep(random.uniform(sleep_min, sleep_max))
        except Exception as e:
            err = str(e)
            is_429 = "429" in err
            is_conn_err = "curl:" in err
            proxy_pool.report_failure(lease.proxy, ban_seconds=ban_429 if is_429 else 30.0)
            if is_conn_err:
                await scrape_pool.invalidate(lease.url)
            if is_429 and pause_on_429 > 0 and pause.is_set():
                asyncio.create_task(_pause_all(pause, pause_on_429))
            log.warning("ERR %-30s geo=%-5s attempt=%d err=%s", job.keyword, job.geo, job.attempt, e)
            try:
                if job.attempt + 1 < MAX_ATTEMPTS:
                    retry = Job(
                        keyword=job.keyword,
                        geo=job.geo,
                        timeframe=job.timeframe,
                        category=job.category,
                        gprop=job.gprop,
                        attempt=job.attempt + 1,
                        kind=job.kind,
                    )
                    await queue.push([retry])
                else:
                    await queue.push_dead(job, reason=str(e))
                    await queue.clear_inflight(job)
            except Exception as queue_err:
                log.error("Failed to re-queue job %s: %s", job.keyword, queue_err)


async def run_worker(concurrency: int, redis_url: str, db_dsn: str, playwright_warmup: bool = False, max_requests: int = 25, ban_429: float = 120.0, sleep_min: float = 0.0, sleep_max: float = 0.0, log_ip: bool = False, pause_on_429: float = 0.0):
    queue = JobQueue(redis_url)
    await queue.connect()

    db = Database(db_dsn)
    await db.connect()

    proxy_pool = await ProxyPool.from_db(db._pool)
    if not proxy_pool._proxies:
        log.warning("Proxy pool is EMPTY — scraping directly from this host's IP. "
                    "Add proxies: python scripts/proxy.py add <url>, then: kill -HUP %d", os.getpid())

    scrape_pool = ScrapePool(max_requests_per_session=max_requests, playwright_warmup=playwright_warmup)
    sem = asyncio.Semaphore(concurrency)
    pause = asyncio.Event()
    pause.set()
    tasks: set[asyncio.Task] = set()
    shutdown = asyncio.Event()

    def _handle_signal(*_):
        log.info("Shutdown signal received, draining...")
        shutdown.set()

    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGINT, _handle_signal)
    loop.add_signal_handler(signal.SIGTERM, _handle_signal)
    loop.add_signal_handler(signal.SIGHUP, lambda: asyncio.create_task(proxy_pool.refresh_from_db(db._pool)))

    log.info("Worker started | concurrency=%d proxies=%d", concurrency, len(proxy_pool._proxies) or 1)

    jobs_processed = 0
    while not shutdown.is_set():
        job = await queue.pop(timeout=2)
        if job is None:
            continue
        task = asyncio.create_task(
            process_job(job, proxy_pool, scrape_pool, db, queue, sem, pause, ban_429, sleep_min, sleep_max, log_ip, pause_on_429)
        )
        tasks.add(task)
        task.add_done_callback(tasks.discard)
        jobs_processed += 1
        if jobs_processed % 100 == 0:
            proxy_pool.log_stats()

    if tasks:
        log.info("Waiting for %d in-flight tasks...", len(tasks))
        await asyncio.gather(*tasks, return_exceptions=True)

    await scrape_pool.close()
    await queue.close()
    await db.close()
    log.info("Worker stopped cleanly")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--redis", type=str, default="redis://localhost:6379")
    parser.add_argument("--db", type=str, default=DSN)
    parser.add_argument("--max-requests", type=int, default=25, help="Max requests per scraper session before rotating")
    parser.add_argument("--rotating", action="store_true", help="Rotating proxy mode: short 429 cooldown (5s) instead of 120s")
    parser.add_argument("--sleep-min", type=float, default=0.0, help="Min sleep between requests per worker (seconds)")
    parser.add_argument("--sleep-max", type=float, default=0.0, help="Max sleep between requests per worker (seconds)")
    parser.add_argument("--log-ip", action="store_true", help="Fetch and log exit IP before each request")
    parser.add_argument("--playwright", action="store_true", help="Use Playwright to get fresh cookies per session")
    parser.add_argument("--pause-on-429", type=float, default=0.0, help="Pause all workers N seconds after first 429 in a burst")
    args = parser.parse_args()
    ban_429 = 5.0 if args.rotating else 120.0
    asyncio.run(run_worker(args.concurrency, args.redis, args.db, args.playwright, args.max_requests, ban_429, args.sleep_min, args.sleep_max, args.log_ip, args.pause_on_429))


if __name__ == "__main__":
    main()