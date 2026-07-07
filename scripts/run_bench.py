"""
Multi-thread proxy benchmark. Usage:
    python run_bench.py <log_dir> <duration_seconds> [threads_per_proxy]
"""
import asyncio
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.test_4proxies import run_proxy_worker, setup_logging


def _load_proxies(path: str) -> list[tuple[str, str]]:
    """Returns [(label, url), ...] from a proxy file."""
    proxies = []
    with open(path) as f:
        for i, line in enumerate(f):
            line = line.strip()
            if line:
                proxies.append((f"proxy-{i+1}", line))
    return proxies


async def main(log_dir: str, duration: int, threads_per_proxy: int = 2):
    proxy_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "proxies_dch.txt")
    proxies = _load_proxies(proxy_file)
    deadline = time.monotonic() + duration
    tasks = []
    for label, url in proxies:
        for i in range(1, threads_per_proxy + 1):
            tasks.append(asyncio.create_task(
                run_proxy_worker(
                    label=f"{label}-{i}",
                    proxy_url=url,
                    min_interval=0.5,
                    max_interval=0.5,
                    deadline=deadline,
                    log_ip=True,
                )
            ))
    results = await asyncio.gather(*tasks, return_exceptions=True)

    log = logging.getLogger(__name__)
    log.info("=" * 70)
    log.info("FINAL  duration=%ds  threads_per_proxy=%d", duration, threads_per_proxy)
    log.info("  %-18s  %5s  %4s  %4s  %5s  %s", "worker", "reqs", "ok", "fail", "rate", "status")
    log.info("  " + "-" * 58)

    # Per-worker stats
    by_proxy: dict[str, dict] = {}
    for r in results:
        if not isinstance(r, dict):
            continue
        status = "DEAD (req#{})".format(r["first_fail"]) if r["dead"] else "alive"
        log.info("  %-18s  %5d  %4d  %4d  %4.0f%%  %s",
                 r["label"], r["total"], r["ok"], r["fail"], r["rate"], status)
        proxy = r["label"].rsplit("-", 1)[0]
        agg = by_proxy.setdefault(proxy, {"total": 0, "ok": 0, "fail": 0})
        agg["total"] += r["total"]
        agg["ok"] += r["ok"]
        agg["fail"] += r["fail"]

    # Per-proxy totals
    log.info("  " + "-" * 58)
    grand_ok = grand_total = 0
    for proxy, agg in by_proxy.items():
        t = agg["total"]
        ok = agg["ok"]
        rday = int(ok / duration * 86400)
        log.info("  %-18s  %5d  %4d  %4d  %4.0f%%  ~%dk req/day",
                 f"[{proxy} total]", t, ok, agg["fail"],
                 ok / t * 100 if t else 0, rday // 1000)
        grand_ok += ok
        grand_total += t

    log.info("  " + "-" * 58)
    rday = int(grand_ok / duration * 86400)
    log.info("  %-18s  %5d  %4d  %4d  %4.0f%%  ~%dk req/day",
             "GRAND TOTAL", grand_total, grand_ok, grand_total - grand_ok,
             grand_ok / grand_total * 100 if grand_total else 0, rday // 1000)


if __name__ == "__main__":
    log_dir = sys.argv[1]
    duration = int(sys.argv[2])
    threads = int(sys.argv[3]) if len(sys.argv) > 3 else 2
    setup_logging(f"{log_dir}/bench.log")
    asyncio.run(main(log_dir, duration, threads))
