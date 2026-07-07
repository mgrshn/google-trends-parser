"""
API load test. Usage:
    python scripts/test_api.py [threads] [duration_seconds]

Default: 3 threads, 300 seconds.
"""
import asyncio
import logging
import os
import random
import sys
import time

import aiohttp

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

API_BASE = os.getenv("API_BASE", "http://localhost:8000")
API_KEY  = os.getenv("API_KEY", "")
GEO      = "US"
TIMEFRAME = "today 12-m"

log = logging.getLogger("api_test")


def _load_keywords(path: str) -> list[str]:
    with open(path) as f:
        return [line.strip() for line in f if line.strip()]


async def worker(label: str, keywords: list[str], deadline: float) -> dict:
    headers = {"X-API-Key": API_KEY} if API_KEY else {}
    stats = {"label": label, "total": 0, "ok": 0, "queued": 0, "error": 0, "latencies": []}

    async with aiohttp.ClientSession(headers=headers) as session:
        while time.monotonic() < deadline:
            kw = random.choice(keywords)
            url = f"{API_BASE}/trends"
            params = {"keyword": kw, "geo": GEO, "timeframe": TIMEFRAME}
            t0 = time.monotonic()
            try:
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=35)) as resp:
                    elapsed = time.monotonic() - t0
                    stats["total"] += 1
                    stats["latencies"].append(elapsed)
                    if resp.status == 200:
                        stats["ok"] += 1
                        data = await resp.json()
                        cached = "cached" if data.get("cached") else "fresh"
                        pts = len(data.get("points", []))
                        log.debug("[%s] 200 %-30s %s  %d pts  %.1fs", label, kw, cached, pts, elapsed)
                    elif resp.status == 202:
                        stats["queued"] += 1
                        log.debug("[%s] 202 %-30s queued       %.1fs", label, kw, elapsed)
                    else:
                        stats["error"] += 1
                        body = await resp.text()
                        log.warning("[%s] %d %-30s %.1fs  %s", label, resp.status, kw, elapsed, body[:80])
            except Exception as exc:
                stats["total"] += 1
                stats["error"] += 1
                log.warning("[%s] ERR %-30s %s", label, kw, exc)

    lats = stats["latencies"]
    stats["p50"] = sorted(lats)[len(lats) // 2] if lats else 0
    stats["p95"] = sorted(lats)[int(len(lats) * 0.95)] if lats else 0
    return stats


async def main(threads: int, duration: int):
    kw_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "keywords.txt")
    keywords = _load_keywords(kw_file)
    log.info("Loaded %d keywords", len(keywords))
    log.info("API: %s  threads=%d  duration=%ds", API_BASE, threads, duration)

    deadline = time.monotonic() + duration
    tasks = [
        asyncio.create_task(worker(f"t{i}", keywords, deadline))
        for i in range(1, threads + 1)
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    log.info("=" * 65)
    log.info("RESULTS  threads=%d  duration=%ds", threads, duration)
    log.info("  %-8s  %5s  %5s  %6s  %5s  %5s  %5s",
             "worker", "total", "ok", "queued", "err", "p50s", "p95s")
    log.info("  " + "-" * 55)

    totals = {"total": 0, "ok": 0, "queued": 0, "error": 0}
    all_lats: list[float] = []

    for r in results:
        if isinstance(r, Exception):
            log.error("  worker crashed: %s", r)
            continue
        log.info("  %-8s  %5d  %5d  %6d  %5d  %5.1f  %5.1f",
                 r["label"], r["total"], r["ok"], r["queued"], r["error"], r["p50"], r["p95"])
        for k in totals:
            totals[k] += r[k]
        all_lats.extend(r["latencies"])

    all_lats.sort()
    p50 = all_lats[len(all_lats) // 2] if all_lats else 0
    p95 = all_lats[int(len(all_lats) * 0.95)] if all_lats else 0
    total = totals["total"]
    ok = totals["ok"]
    rph = int(ok / duration * 3600)

    log.info("  " + "-" * 55)
    log.info("  %-8s  %5d  %5d  %6d  %5d  %5.1f  %5.1f   ~%d req/h",
             "TOTAL", total, ok, totals["queued"], totals["error"], p50, p95, rph)
    log.info("  ok rate: %.1f%%", ok / total * 100 if total else 0)


if __name__ == "__main__":
    threads  = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    duration = int(sys.argv[2]) if len(sys.argv) > 2 else 300

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(f"logs/api_test_{threads}t_{duration}s.log"),
        ],
    )
    asyncio.run(main(threads, duration))
