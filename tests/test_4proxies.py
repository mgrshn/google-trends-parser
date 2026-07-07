"""
Тест живучести прокси с таймером. Все прокси + домашний IP параллельно.

Usage:
    python test_4proxies.py                          # все прокси, 5 минут
    python test_4proxies.py --duration 300           # явно 5 минут
    python test_4proxies.py --min-interval 8 --max-interval 20
"""
import asyncio
import argparse
import itertools
import logging
import random
import time

from curl_cffi.requests import AsyncSession
from app.scraper import GoogleTrendsScraper, load_cookies_from_file, IMPERSONATE
from app.cookie_warmer import get_google_cookies

PROXIES = [
    ("216.227.222.99:12323  [DC]",  "http://14ab0cc5bb8c1:94dfb66316@216.227.222.99:12323"),
    ("64.224.18.37:12323   [DC]",   "http://14ab0cc5bb8c1:94dfb66316@64.224.18.37:12323"),
    ("209.200.249.64:12323 [DC]",   "http://14ab0cc5bb8c1:94dfb66316@209.200.249.64:12323"),
    ("185.234.59.13:21527  [MOB]",  "http://AtCuVE:aD4YFetkuuBe@185.234.59.13:21527"),
    ("us.dch.dcproxy.com   [DC]",   "http://cIzgRB7t:B49q5q6b@us.dch.dcproxy.com:10000"),
    ("direct (home IP)",            None),
]

KEYWORDS = [
    ("best air fryer",        "US", "today 12-m"),
    ("tax return",            "US", "today 3-m"),
    ("learn guitar",          "US", "today 12-m"),
    ("protein powder",        "GB", "today 12-m"),
    ("road trip",             "US", "today 12-m"),
    ("meal prep",             "US", "today 3-m"),
    ("skin care routine",     "US", "today 12-m"),
    ("budget travel",         "AU", "today 12-m"),
    ("photography tips",      "CA", "today 12-m"),
    ("wedding planning",      "US", "today 12-m"),
    ("side hustle",           "US", "today 12-m"),
    ("minimalism",            "DE", "today 12-m"),
    ("plant based diet",      "GB", "today 12-m"),
    ("diy home repair",       "US", "today 12-m"),
    ("cat food",              "US", "today 12-m"),
    ("wine tasting",          "FR", "today 12-m"),
    ("college tips",          "US", "today 12-m"),
    ("gardening ideas",       "US", "today 12-m"),
    ("candle making",         "US", "today 12-m"),
    ("sleep better",          "US", "today 3-m"),
    ("car maintenance",       "US", "today 12-m"),
    ("online dating",         "US", "today 12-m"),
    ("board games",           "DE", "today 12-m"),
    ("zero waste",            "SE", "today 12-m"),
    ("real estate investing", "US", "today 12-m"),
]

DEAD_THRESHOLD = 5


async def get_exit_ip(proxy_url: str | None) -> str:
    try:
        async with AsyncSession(
            impersonate=IMPERSONATE,
            proxies={"https": proxy_url, "http": proxy_url} if proxy_url else None,
            timeout=8,
        ) as s:
            r = await s.get("https://ifconfig.me/ip")
            return r.text.strip()
    except Exception as e:
        return f"err"


def setup_logging(log_file: str):
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    file_handler = logging.FileHandler(log_file, encoding="utf-8", mode="w")
    file_handler.setFormatter(fmt)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(file_handler)
    root.addHandler(console_handler)


log = logging.getLogger(__name__)


async def run_proxy_worker(label: str, proxy_url: str | None, min_interval: float, max_interval: float, deadline: float, cookies: dict | None = None, playwright: bool = False, log_ip: bool = False):
    ok = 0
    fail = 0
    consec_fail = 0
    first_fail_at = None
    req_n = 0
    dead = False

    if playwright:
        cookies = await get_google_cookies(proxy_url=proxy_url)
        log.info("[%s] Playwright warmup done, got %d cookies (AEC=%s)", label, len(cookies), "AEC" in cookies)

    scraper = GoogleTrendsScraper(proxy=proxy_url, cookies=cookies or None)
    await scraper.__aenter__()

    try:
        for keyword, geo, timeframe in itertools.cycle(KEYWORDS):
            if time.monotonic() >= deadline:
                log.info("[%s] TIME UP", label)
                break

            req_n += 1
            t0 = time.monotonic()
            ts = time.strftime("%H:%M:%S")
            exit_ip = await get_exit_ip(proxy_url) if log_ip else ""
            ip_tag = f" ip={exit_ip}" if exit_ip else ""
            try:
                result = await scraper.fetch(keyword, geo, timeframe)
                pts = sum(1 for p in result.points if p.has_data)
                elapsed = time.monotonic() - t0
                ok += 1
                consec_fail = 0
                log.info(
                    "[%s] req#%-3d [%s] OK    %-24s pts=%-3d %.1fs | %d ok / %d fail%s",
                    label, req_n, ts, keyword, pts, elapsed, ok, fail, ip_tag,
                )
            except Exception as e:
                elapsed = time.monotonic() - t0
                fail += 1
                consec_fail += 1
                if first_fail_at is None:
                    first_fail_at = req_n
                log.warning(
                    "[%s] req#%-3d [%s] FAIL  %-24s %.1fs | %d ok / %d fail  consec=%d%s  ERR: %s",
                    label, req_n, ts, keyword, elapsed, ok, fail, consec_fail, ip_tag, e,
                )
                if "curl:" in str(e) or "429" in str(e):
                    try:
                        await scraper.__aexit__(None, None, None)
                    except Exception:
                        pass
                    if playwright:
                        cookies = await get_google_cookies(proxy_url=proxy_url)
                        log.info("[%s] Playwright re-warmup, got %d cookies (AEC=%s)", label, len(cookies), "AEC" in cookies)
                    scraper = GoogleTrendsScraper(proxy=proxy_url, cookies=cookies or None)
                    await scraper.__aenter__()

                if consec_fail >= DEAD_THRESHOLD:
                    log.error("[%s] DEAD  after %d consecutive fails (req #%d). First fail: req #%s.",
                              label, consec_fail, req_n, first_fail_at)
                    dead = True
                    break

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            if min_interval > 0 or max_interval > 0:
                await asyncio.sleep(min(random.uniform(min_interval, max_interval), remaining))

    finally:
        try:
            await scraper.__aexit__(None, None, None)
        except Exception:
            pass

    total = ok + fail
    rate = ok / total * 100 if total else 0
    status = "DEAD" if dead else "TIME UP"
    log.info("[%s] SUMMARY  status=%s  requests=%d  ok=%d  fail=%d  rate=%.0f%%  first_fail=%s",
             label, status, total, ok, fail, rate,
             f"req#{first_fail_at}" if first_fail_at else "never")
    return {"label": label, "total": total, "ok": ok, "fail": fail, "rate": rate,
            "first_fail": first_fail_at, "dead": dead}


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-interval", type=float, default=0.0)
    parser.add_argument("--max-interval", type=float, default=0.0)
    parser.add_argument("--duration", type=int, default=300, help="Test duration in seconds")
    parser.add_argument("--log", type=str, default="test_4proxies.log")
    parser.add_argument("--direct", action="store_true", help="Test home IP only")
    parser.add_argument("--labels", type=str, default=None, help="Comma-separated label substrings to include (e.g. 'dch,home')")
    parser.add_argument("--cookies", type=str, default=None, help="Path to file with Cookie header string")
    parser.add_argument("--playwright", action="store_true", help="Use Playwright warmup per worker")
    parser.add_argument("--log-ip", action="store_true", help="Fetch and log exit IP before each request")
    args = parser.parse_args()

    setup_logging(args.log)

    cookies = load_cookies_from_file(args.cookies) if args.cookies else None
    if cookies:
        log.info("Loaded %d cookies from %s", len(cookies), args.cookies)

    deadline = time.monotonic() + args.duration

    all_workers = list(PROXIES) + [("direct (home IP)", None)]
    if args.direct:
        workers = [("direct (home IP)", None)]
    elif args.labels:
        filters = [f.strip().lower() for f in args.labels.split(",")]
        workers = [(l, u) for l, u in all_workers if any(f in l.lower() for f in filters)]
    else:
        workers = all_workers

    log.info("=" * 70)
    log.info("Workers: %d | interval: %.0f–%.0fs | duration: %ds | dead after: %d consec fails | cookies: %s | playwright: %s | log_ip: %s",
             len(workers), args.min_interval, args.max_interval, args.duration, DEAD_THRESHOLD,
             f"{len(cookies)} loaded" if cookies else "none",
             "yes" if args.playwright else "no",
             "yes" if args.log_ip else "no")
    log.info("=" * 70)

    tasks = [
        asyncio.create_task(run_proxy_worker(label, proxy_url, args.min_interval, args.max_interval, deadline, cookies, args.playwright, args.log_ip))
        for label, proxy_url in workers
    ]

    try:
        results = await asyncio.gather(*tasks, return_exceptions=True)
    except KeyboardInterrupt:
        for t in tasks:
            t.cancel()
        results = await asyncio.gather(*tasks, return_exceptions=True)

    log.info("=" * 70)
    log.info("FINAL RESULTS (duration=%ds):", args.duration)
    log.info("  %-30s  %5s  %4s  %4s  %5s  %s", "proxy", "reqs", "ok", "fail", "rate", "status")
    log.info("  " + "-" * 65)
    for r in results:
        if isinstance(r, dict):
            status = "DEAD (req#{})".format(r["first_fail"]) if r["dead"] else "alive"
            log.info("  %-30s  %5d  %4d  %4d  %4.0f%%  %s",
                     r["label"], r["total"], r["ok"], r["fail"], r["rate"], status)


if __name__ == "__main__":
    asyncio.run(main())
