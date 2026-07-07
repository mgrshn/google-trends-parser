"""
DCH-only multi-thread benchmark. Usage:
    python run_dch_only.py <log_dir> <duration_seconds> <threads>
"""
import asyncio
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.test_4proxies import run_proxy_worker, setup_logging


def _load_first_proxy(path: str) -> str:
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                return line
    raise ValueError(f"No proxies found in {path}")


async def main(log_dir: str, duration: int, threads: int):
    proxy_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "proxies_dch.txt")
    proxy_url = _load_first_proxy(proxy_file)
    deadline = time.monotonic() + duration
    tasks = [
        asyncio.create_task(run_proxy_worker(
            label=f"dch-{i}",
            proxy_url=proxy_url,
            min_interval=0.5,
            max_interval=0.5,
            deadline=deadline,
            log_ip=True,
        ))
        for i in range(1, threads + 1)
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    log = logging.getLogger(__name__)
    log.info("=" * 70)
    log.info("FINAL  proxy=dch  threads=%d  duration=%ds", threads, duration)
    log.info("  %-12s  %5s  %4s  %4s  %5s  %s", "worker", "reqs", "ok", "fail", "rate", "status")
    log.info("  " + "-" * 50)
    total_ok = total_fail = 0
    for r in results:
        if not isinstance(r, dict):
            continue
        status = "DEAD (req#{})".format(r["first_fail"]) if r["dead"] else "alive"
        log.info("  %-12s  %5d  %4d  %4d  %4.0f%%  %s",
                 r["label"], r["total"], r["ok"], r["fail"], r["rate"], status)
        total_ok += r["ok"]
        total_fail += r["fail"]
    total = total_ok + total_fail
    rday = int(total_ok / duration * 86400)
    log.info("  " + "-" * 50)
    log.info("  %-12s  %5d  %4d  %4d  %4.0f%%  ~%dk req/day",
             "TOTAL", total, total_ok, total_fail,
             total_ok / total * 100 if total else 0, rday // 1000)


if __name__ == "__main__":
    log_dir = sys.argv[1]
    duration = int(sys.argv[2])
    threads = int(sys.argv[3])
    setup_logging(f"{log_dir}/bench.log")
    asyncio.run(main(log_dir, duration, threads))