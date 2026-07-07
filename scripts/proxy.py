"""
CLI for managing proxies in the database.

Usage:
    python scripts/proxy.py add http://user:pass@host:port
    python scripts/proxy.py list
    python scripts/proxy.py enable 2
    python scripts/proxy.py disable 2
    python scripts/proxy.py remove 2
"""
import argparse
import asyncio
import os

import asyncpg

DSN = os.getenv("DB_DSN", "postgresql://trends:trends@localhost:5432/trends")


async def cmd_add(conn: asyncpg.Connection, url: str):
    await conn.execute(
        "INSERT INTO proxies (url) VALUES ($1) ON CONFLICT (url) DO UPDATE SET enabled = true",
        url,
    )
    print(f"Added: {url}")


async def cmd_list(conn: asyncpg.Connection):
    rows = await conn.fetch(
        "SELECT id, url, enabled, added_at, success_count, fail_count FROM proxies ORDER BY id"
    )
    if not rows:
        print("No proxies in DB")
        return
    print(f"{'ID':<4} {'Enabled':<8} {'Success':<8} {'Fail':<6} URL")
    print("-" * 70)
    for r in rows:
        status = "yes" if r["enabled"] else "no"
        host = r["url"].split("@")[-1] if "@" in r["url"] else r["url"]
        print(f"{r['id']:<4} {status:<8} {r['success_count']:<8} {r['fail_count']:<6} {host}")


async def cmd_enable(conn: asyncpg.Connection, proxy_id: int):
    result = await conn.execute("UPDATE proxies SET enabled = true WHERE id = $1", proxy_id)
    print(f"Enabled proxy #{proxy_id}" if "1" in result else f"Proxy #{proxy_id} not found")


async def cmd_disable(conn: asyncpg.Connection, proxy_id: int):
    result = await conn.execute("UPDATE proxies SET enabled = false WHERE id = $1", proxy_id)
    print(f"Disabled proxy #{proxy_id}" if "1" in result else f"Proxy #{proxy_id} not found")


async def cmd_remove(conn: asyncpg.Connection, proxy_id: int):
    result = await conn.execute("DELETE FROM proxies WHERE id = $1", proxy_id)
    print(f"Removed proxy #{proxy_id}" if "1" in result else f"Proxy #{proxy_id} not found")


async def main():
    parser = argparse.ArgumentParser(description="Manage proxies in DB")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_add = sub.add_parser("add", help="Add proxy")
    p_add.add_argument("url", help="Proxy URL: http://user:pass@host:port")

    sub.add_parser("list", help="List all proxies")

    for name in ("enable", "disable", "remove"):
        p = sub.add_parser(name)
        p.add_argument("id", type=int, help="Proxy ID (from list)")

    args = parser.parse_args()
    conn = await asyncpg.connect(DSN)
    try:
        if args.cmd == "add":
            await cmd_add(conn, args.url)
        elif args.cmd == "list":
            await cmd_list(conn)
        elif args.cmd == "enable":
            await cmd_enable(conn, args.id)
        elif args.cmd == "disable":
            await cmd_disable(conn, args.id)
        elif args.cmd == "remove":
            await cmd_remove(conn, args.id)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())