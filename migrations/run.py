"""
Apply pending SQL migrations.

Usage:
    python migrations/run.py
    python migrations/run.py --dsn postgresql://user:pass@host/db
"""
import asyncio
import argparse
import os
import asyncpg

MIGRATIONS_DIR = os.path.dirname(__file__)
DSN_DEFAULT = os.getenv("DB_DSN", "postgresql://trends:trends@localhost:5432/trends")


async def run(dsn: str):
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                id         SERIAL PRIMARY KEY,
                filename   TEXT        NOT NULL UNIQUE,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)

        applied = {r["filename"] for r in await conn.fetch("SELECT filename FROM schema_migrations")}

        files = sorted(f for f in os.listdir(MIGRATIONS_DIR) if f.endswith(".sql"))
        pending = [f for f in files if f not in applied]

        if not pending:
            print("Nothing to apply, all migrations are up to date.")
            return

        for filename in pending:
            path = os.path.join(MIGRATIONS_DIR, filename)
            sql = open(path).read()
            print(f"Applying {filename}...")
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute(
                    "INSERT INTO schema_migrations (filename) VALUES ($1)", filename
                )
            print(f"  ✓ {filename}")

        print(f"Done. Applied {len(pending)} migration(s).")
    finally:
        await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dsn", default=DSN_DEFAULT)
    args = parser.parse_args()
    asyncio.run(run(args.dsn))
