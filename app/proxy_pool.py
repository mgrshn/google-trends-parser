import asyncio
import logging
import time
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


@dataclass
class ProxyEntry:
    url: str
    fail_count: int = 0
    success_count: int = 0
    ban_until: float = 0.0
    in_use: int = 0
    last_used: float = 0.0

    @property
    def host(self) -> str:
        try:
            return self.url.split("@")[-1]
        except Exception:
            return self.url

    def is_available(self, request_interval: float = 0.0) -> bool:
        now = time.time()
        return now > self.ban_until and now >= self.last_used + request_interval

    def cooldown(self, seconds: float):
        self.ban_until = time.time() + seconds
        self.fail_count += 1
        log.warning(
            "PROXY BAN  %s  fail_count=%d  cooldown=%.0fs",
            self.host, self.fail_count, seconds,
        )


class ProxyPool:
    def __init__(self, proxy_urls: list[str], request_interval: float = 0.0):
        self._proxies = [ProxyEntry(url=u) for u in proxy_urls]
        self._lock = asyncio.Lock()
        self._request_interval = request_interval

    @classmethod
    async def from_db(cls, pg_pool, request_interval: float = 0.0) -> "ProxyPool":
        rows = await pg_pool.fetch("SELECT url FROM proxies WHERE enabled = true")
        urls = [r["url"] for r in rows]
        instance = cls(urls, request_interval=request_interval)
        log.info("ProxyPool loaded: %d proxies from DB", len(urls))
        return instance

    @classmethod
    def from_file(cls, path: str, request_interval: float = 0.0) -> "ProxyPool":
        with open(path) as f:
            urls = [line.strip() for line in f if line.strip()]
        instance = cls(urls, request_interval=request_interval)
        log.info("ProxyPool loaded: %d proxies from %s", len(urls), path)
        return instance

    @classmethod
    def direct(cls) -> "ProxyPool":
        return cls([])

    async def refresh_from_db(self, pg_pool):
        rows = await pg_pool.fetch("SELECT url FROM proxies WHERE enabled = true")
        new_urls = {r["url"] for r in rows}
        async with self._lock:
            existing = {p.url: p for p in self._proxies}
            if new_urls == set(existing):
                return
            self._proxies = [
                existing[url] if url in existing else ProxyEntry(url=url)
                for url in new_urls
            ]
            log.info("ProxyPool refreshed: %d proxies", len(self._proxies))

    async def acquire(self, timeout: float = 30.0) -> "ProxyLease":
        deadline = time.time() + timeout
        while time.time() < deadline:
            async with self._lock:
                if not self._proxies:
                    return ProxyLease(pool=self, proxy=None)
                available = [p for p in self._proxies if p.is_available(self._request_interval)]
                if available:
                    proxy = min(available, key=lambda p: p.in_use)
                    proxy.in_use += 1
                    return ProxyLease(pool=self, proxy=proxy)
            await asyncio.sleep(0.5)
        raise TimeoutError("No proxy available within timeout")

    def report_success(self, proxy: ProxyEntry | None):
        if proxy is None:
            return
        proxy.success_count += 1
        proxy.fail_count = max(0, proxy.fail_count - 1)
        proxy.in_use = max(0, proxy.in_use - 1)
        proxy.last_used = time.time()

    def report_failure(self, proxy: ProxyEntry | None, ban_seconds: float = 60.0):
        if proxy is None:
            return
        proxy.cooldown(ban_seconds)
        proxy.in_use = max(0, proxy.in_use - 1)

    def stats(self) -> list[dict]:
        return [
            {
                "proxy": p.host,
                "success": p.success_count,
                "fail": p.fail_count,
                "in_use": p.in_use,
                "banned": not p.is_available(),
                "ban_seconds_left": max(0, round(p.ban_until - time.time())),
            }
            for p in self._proxies
        ]

    def log_stats(self):
        for s in self.stats():
            status = f"BANNED({s['ban_seconds_left']}s)" if s["banned"] else "OK"
            log.info(
                "PROXY STAT  %s  ok=%d  fail=%d  in_use=%d  status=%s",
                s["proxy"], s["success"], s["fail"], s["in_use"], status,
            )


class ProxyLease:
    def __init__(self, pool: ProxyPool, proxy: ProxyEntry | None):
        self._pool = pool
        self.proxy = proxy
        self.url = proxy.url if proxy else None