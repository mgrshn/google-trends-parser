"""
Enqueue trending-jobs for all (or selected) geos.
Run by cron every few hours to accumulate trending keywords history.

Usage:
    python scripts/enqueue_trending.py                # все гео, окно 24ч
    python scripts/enqueue_trending.py --geos US,DE   # выборочно
    python scripts/enqueue_trending.py --hours 4      # окно 4 часа
"""
import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.job_queue import Job, JobQueue

# Гео, поддерживаемые Google Trends "Trending Now"
GEOS = [
    "US", "GB", "DE", "FR", "IT", "ES", "PT", "NL", "BE", "AT", "CH", "IE",
    "PL", "CZ", "SK", "HU", "RO", "BG", "GR", "DK", "FI", "NO", "SE",
    "EE", "LV", "LT", "UA", "TR", "RU", "RS", "HR", "SI",
    "CA", "MX", "BR", "AR", "CL", "CO", "PE", "VE", "EC", "UY",
    "AU", "NZ", "JP", "KR", "TW", "HK", "SG", "MY", "TH", "VN", "PH", "ID", "IN", "PK", "BD", "LK",
    "IL", "SA", "AE", "EG", "NG", "KE", "ZA", "MA", "DZ",
]


async def main(geos: list[str], hours: int, redis_url: str):
    queue = JobQueue(redis_url)
    await queue.connect()
    jobs = [Job(keyword="", geo=g, timeframe=str(hours), kind="trending") for g in geos]
    pushed = await queue.push_unique(jobs)
    print(f"Enqueued {pushed}/{len(jobs)} trending jobs (hours={hours})")
    await queue.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--geos", type=str, default="", help="Comma-separated geo codes; empty = all supported")
    parser.add_argument("--hours", type=int, default=24, choices=[4, 24, 48, 168], help="Trending window")
    parser.add_argument("--redis", type=str, default=os.getenv("REDIS_URL", "redis://localhost:6379"))
    args = parser.parse_args()
    geo_list = [g.strip().upper() for g in args.geos.split(",") if g.strip()] or GEOS
    asyncio.run(main(geo_list, args.hours, args.redis))