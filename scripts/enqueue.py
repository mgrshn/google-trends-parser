"""
Load keywords into the Redis queue.

Usage:
    # Single keyword
    python enqueue.py --keyword "bitcoin" --geo US

    # From file (one keyword per line)
    python enqueue.py --file keywords.txt --geo RU

    # Multiple geos from file
    python enqueue.py --file keywords.txt --geo US,RU,DE,GB

    # Check queue size
    python enqueue.py --status
"""
import argparse
import asyncio

from app.job_queue import Job, JobQueue

TIMEFRAME = "today 12-m"


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--keyword", type=str)
    parser.add_argument("--file", type=str, help="File with keywords, one per line")
    parser.add_argument("--geo", type=str, default="US", help="Comma-separated geos: US,RU,DE")
    parser.add_argument("--timeframe", type=str, default=TIMEFRAME)
    parser.add_argument("--redis", type=str, default="redis://localhost:6379")
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()

    queue = JobQueue(args.redis)
    await queue.connect()

    if args.status:
        size = await queue.size()
        dead = await queue.dead_size()
        print(f"Queue: {size} jobs | Dead: {dead} jobs")
        await queue.close()
        return

    keywords = []
    if args.keyword:
        keywords.append(args.keyword)
    if args.file:
        with open(args.file) as f:
            keywords.extend(line.strip() for line in f if line.strip())

    geos = [g.strip() for g in args.geo.split(",")]

    jobs = [
        Job(keyword=kw, geo=geo, timeframe=args.timeframe)
        for kw in keywords
        for geo in geos
    ]

    BATCH = 1000
    for i in range(0, len(jobs), BATCH):
        await queue.push(jobs[i : i + BATCH])

    print(f"Enqueued {len(jobs)} jobs ({len(keywords)} keywords × {len(geos)} geos)")
    await queue.close()


if __name__ == "__main__":
    asyncio.run(main())