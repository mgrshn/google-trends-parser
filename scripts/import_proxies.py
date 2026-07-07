"""
Import proxies from a text file into the database.

Usage:
    python scripts/import_proxies.py proxies.txt
    python scripts/import_proxies.py proxies.txt --db postgresql://...
"""
import argparse
import asyncio
import os

import asyncpg

DSN = os.getenv("DB_DSN", "postgresql://trends:trends@localhost:5432/trends")


async def main(path: str, dsn: str):
    with open(path) as f:
        urls = [line.strip() for line in f if line.strip()]

    if not urls:
        print(f"No proxies found in {path}")
        return

    conn = await asyncpg.connect(dsn)
    try:
        inserted = 0
        skipped = 0
        for url in urls:
            result = await conn.execute(
                "INSERT INTO proxies (url) VALUES ($1) ON CONFLICT (url) DO NOTHING",
                url,
            )
            if result == "INSERT 0 1":
                inserted += 1
                host = url.split("@")[-1] if "@" in url else url
                print(f"  + {host}")
            else:
                skipped += 1
        print(f"\nDone: {inserted} imported, {skipped} already existed")
    finally:
        await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("file", help="Path to proxy list (one URL per line)")
    parser.add_argument("--db", default=DSN, help="PostgreSQL DSN")
    args = parser.parse_args()
    asyncio.run(main(args.file, args.db))