"""
Retention policy cleanup:
  - Delete trend_series (+ their points) not accessed in 7 days
  - Drop trend_points partitions older than 12 months
  - Trim dead queue to 1000 entries

Usage:
    python scripts/cleanup.py [--dry-run]
    python scripts/cleanup.py --stale-days 7 --partition-months 12 --dead-limit 1000
"""
import argparse
import asyncio
import datetime
import logging
import os

import asyncpg
import redis.asyncio as aioredis

DSN = os.getenv("DB_DSN", "postgresql://trends:trends@localhost:5432/trends")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


async def cleanup_stale_series(conn: asyncpg.Connection, stale_days: int, dry_run: bool):
    cutoff = datetime.datetime.now(tz=datetime.timezone.utc) - datetime.timedelta(days=stale_days)

    stale = await conn.fetch(
        """
        SELECT id FROM trend_series
        WHERE COALESCE(last_accessed_at, last_scraped_at) < $1
           OR (last_scraped_at IS NULL AND last_accessed_at IS NULL)
        """,
        cutoff,
    )
    if not stale:
        log.info("No stale series found (cutoff: %s)", cutoff.date())
        return

    ids = [r["id"] for r in stale]
    log.info("Found %d stale series (not accessed in %d days)", len(ids), stale_days)

    if dry_run:
        log.info("[dry-run] Would delete %d series and their points", len(ids))
        return

    async with conn.transaction():
        deleted_points = await conn.fetchval(
            "WITH d AS (DELETE FROM trend_points WHERE series_id = ANY($1::int[]) RETURNING 1) SELECT count(*) FROM d",
            ids,
        )
        deleted_series = await conn.fetchval(
            "WITH d AS (DELETE FROM trend_series WHERE id = ANY($1::int[]) RETURNING 1) SELECT count(*) FROM d",
            ids,
        )
    log.info("Deleted %d series, %d points", deleted_series, deleted_points)


async def cleanup_stale_compares(conn: asyncpg.Connection, stale_days: int, dry_run: bool):
    cutoff = datetime.datetime.now(tz=datetime.timezone.utc) - datetime.timedelta(days=stale_days)

    for table in ("compare_cache", "widget_cache"):
        count = await conn.fetchval(
            f"SELECT count(*) FROM {table} WHERE COALESCE(last_accessed_at, last_scraped_at) < $1",
            cutoff,
        )
        if not count:
            log.info("No stale %s entries found", table)
            continue

        if dry_run:
            log.info("[dry-run] Would delete %d %s entries", count, table)
            continue

        await conn.execute(
            f"DELETE FROM {table} WHERE COALESCE(last_accessed_at, last_scraped_at) < $1",
            cutoff,
        )
        log.info("Deleted %d %s entries", count, table)


async def cleanup_old_trending(conn: asyncpg.Connection, trending_days: int, dry_run: bool):
    cutoff = datetime.datetime.now(tz=datetime.timezone.utc) - datetime.timedelta(days=trending_days)
    count = await conn.fetchval(
        "SELECT count(*) FROM trending_searches WHERE last_seen_at < $1", cutoff,
    )
    if not count:
        log.info("No old trending entries found")
        return
    if dry_run:
        log.info("[dry-run] Would delete %d trending entries older than %d days", count, trending_days)
        return
    await conn.execute("DELETE FROM trending_searches WHERE last_seen_at < $1", cutoff)
    log.info("Deleted %d trending entries", count)


async def cleanup_old_partitions(conn: asyncpg.Connection, partition_months: int, dry_run: bool):
    cutoff = datetime.date.today().replace(day=1)
    for _ in range(partition_months):
        month = cutoff.month - 1
        year = cutoff.year if month > 0 else cutoff.year - 1
        month = month if month > 0 else 12
        cutoff = cutoff.replace(year=year, month=month)

    partitions = await conn.fetch(
        """
        SELECT child.relname AS name
        FROM pg_inherits
        JOIN pg_class parent ON pg_inherits.inhparent = parent.oid
        JOIN pg_class child  ON pg_inherits.inhrelid  = child.oid
        WHERE parent.relname = 'trend_points'
        """
    )

    to_drop = []
    for r in partitions:
        name = r["name"]
        parts = name.split("_")
        if len(parts) >= 4:
            try:
                year, month = int(parts[-2]), int(parts[-1])
                partition_date = datetime.date(year, month, 1)
                if partition_date < cutoff:
                    to_drop.append(name)
            except ValueError:
                pass

    if not to_drop:
        log.info("No old partitions to drop (cutoff: %s)", cutoff)
        return

    log.info("Found %d partition(s) to drop: %s", len(to_drop), to_drop)
    if dry_run:
        log.info("[dry-run] Would drop: %s", to_drop)
        return

    for name in to_drop:
        await conn.execute(f"DROP TABLE IF EXISTS {name}")
        log.info("Dropped partition %s", name)


async def cleanup_dead_queue(r: aioredis.Redis, dead_limit: int, dry_run: bool):
    size = await r.llen("trends:dead")
    if size <= dead_limit:
        log.info("Dead queue size %d — within limit (%d), skipping", size, dead_limit)
        return

    log.info("Dead queue size %d — trimming to %d", size, dead_limit)
    if dry_run:
        log.info("[dry-run] Would LTRIM trends:dead 0 %d", dead_limit - 1)
        return

    await r.ltrim("trends:dead", 0, dead_limit - 1)
    log.info("Dead queue trimmed to %d entries", dead_limit)


async def main(stale_days: int, partition_months: int, dead_limit: int, trending_days: int, dry_run: bool):
    if dry_run:
        log.info("=== DRY RUN — no changes will be made ===")

    conn = await asyncpg.connect(DSN)
    r = aioredis.from_url(REDIS_URL, decode_responses=True)
    try:
        await cleanup_stale_series(conn, stale_days, dry_run)
        await cleanup_stale_compares(conn, stale_days, dry_run)
        await cleanup_old_trending(conn, trending_days, dry_run)
        await cleanup_old_partitions(conn, partition_months, dry_run)
        await cleanup_dead_queue(r, dead_limit, dry_run)
    finally:
        await conn.close()
        await r.aclose()

    log.info("Cleanup done")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--stale-days", type=int, default=7, help="Delete series not accessed in N days")
    parser.add_argument("--partition-months", type=int, default=12, help="Drop partitions older than N months")
    parser.add_argument("--dead-limit", type=int, default=1000, help="Max entries to keep in dead queue")
    parser.add_argument("--trending-days", type=int, default=90, help="Delete trending history older than N days")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be deleted without doing it")
    args = parser.parse_args()
    asyncio.run(main(args.stale_days, args.partition_months, args.dead_limit, args.trending_days, args.dry_run))