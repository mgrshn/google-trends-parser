import asyncio
import json
import logging
import random
from dataclasses import dataclass
from typing import Optional

from curl_cffi.requests import AsyncSession

log = logging.getLogger(__name__)


EXPLORE_URL = "https://trends.google.com/trends/api/explore"
MULTILINE_URL = "https://trends.google.com/trends/api/widgetdata/multiline"
GEO_URL = "https://trends.google.com/trends/api/widgetdata/comparedgeo"
RELATED_URL = "https://trends.google.com/trends/api/widgetdata/relatedsearches"
TRENDING_URL = "https://trends.google.com/_/TrendsUi/data/batchexecute"
WARMUP_URL = "https://trends.google.com/"

# Импersonируем Chrome — curl_cffi подменяет TLS fingerprint на уровне пакетов
IMPERSONATE = "chrome124"


@dataclass
class TrendPoint:
    timestamp: int
    date_label: str
    value: int
    has_data: bool


@dataclass
class TrendResult:
    keyword: str
    geo: str
    timeframe: str
    points: list[TrendPoint]
    bytes_received: int = 0


@dataclass
class CompareResult:
    keywords: list[str]
    geo: str
    timeframe: str
    points: list[dict]  # {"timestamp": int, "values": [int, ...]} — values[i] соответствует keywords[i]
    bytes_received: int = 0


def _strip_xssi(text: str) -> str:
    return text[text.index("\n") + 1:]



class GoogleTrendsScraper:
    def __init__(self, proxy: Optional[str] = None, max_retries: int = 3, cookies: Optional[dict] = None):
        self.proxy = proxy
        self.max_retries = max_retries
        self._cookies = cookies  # если переданы — warmup пропускаем, куки уже есть
        self._session: Optional[AsyncSession] = None
        self._warmed_up = bool(cookies)
        self._request_count = 0
        self._fetch_bytes = 0

    async def __aenter__(self):
        self._session = AsyncSession(
            impersonate=IMPERSONATE,
            proxies={"https": self.proxy, "http": self.proxy} if self.proxy else None,
            timeout=30,
        )
        if self._cookies:
            self._session.cookies.update(self._cookies)
            log.debug("Loaded %d cookies from cookie jar", len(self._cookies))
        return self

    async def __aexit__(self, *_):
        if self._session:
            await self._session.close()

    async def _warm_up(self):
        if self._warmed_up:
            return
        await self._session.get(WARMUP_URL)
        self._warmed_up = True
        await asyncio.sleep(random.uniform(0.5, 1.5))

    async def _request(self, method: str, url: str, params: dict | None = None, data: dict | None = None, headers: dict | None = None) -> str:
        for attempt in range(self.max_retries):
            resp = await self._session.request(method, url, params=params, data=data, headers=headers)
            self._request_count += 1
            if resp.status_code == 200:
                self._fetch_bytes += len(resp.content)
                return resp.text
            if resp.status_code == 429:
                wait = 2 ** attempt + random.uniform(0, 1)
                await asyncio.sleep(wait)
                continue
            resp.raise_for_status()
        raise RuntimeError(f"Failed after {self.max_retries} retries: 429 Too Many Requests")

    async def _get(self, url: str, params: dict) -> str:
        return await self._request("GET", url, params=params)

    async def _get_tokens(self, items: list[dict], category: int = 0, gprop: str = "") -> dict:
        req = {
            "comparisonItem": items,
            "category": category,
            "property": gprop,
        }
        text = await self._get(EXPLORE_URL, {"hl": "en-US", "tz": "180", "req": json.dumps(req, separators=(",", ":"))})
        data = json.loads(_strip_xssi(text))
        return {w["id"]: w for w in data["widgets"]}

    async def _get_timeline(self, widget: dict, geo: str) -> list[dict]:
        text = await self._get(
            MULTILINE_URL,
            {
                "hl": "en-US",
                "tz": "180",
                "req": json.dumps(widget["request"], separators=(",", ":")),
                "token": widget["token"],
                "geo": geo,
            },
        )
        data = json.loads(_strip_xssi(text))
        return data["default"]["timelineData"]

    async def fetch(self, keyword: str, geo: str = "", timeframe: str = "today 1-m", category: int = 0, gprop: str = "") -> TrendResult:
        self._fetch_bytes = 0
        await self._warm_up()
        widgets = await self._get_tokens([{"keyword": keyword, "geo": geo, "time": timeframe}], category, gprop)
        timeline = await self._get_timeline(widgets["TIMESERIES"], geo)
        points = [
            TrendPoint(
                timestamp=int(item["time"]),
                date_label=item["formattedTime"],
                value=item["value"][0],
                has_data=item["hasData"][0],
            )
            for item in timeline
        ]
        return TrendResult(keyword=keyword, geo=geo, timeframe=timeframe, points=points, bytes_received=self._fetch_bytes)

    async def fetch_compare(self, keywords: list[str], geo: str = "", timeframe: str = "today 1-m", category: int = 0, gprop: str = "") -> CompareResult:
        """До 5 ключей одним запросом — значения нормализованы к общему пику (сравнимы между собой)."""
        self._fetch_bytes = 0
        await self._warm_up()
        items = [{"keyword": k, "geo": geo, "time": timeframe} for k in keywords]
        widgets = await self._get_tokens(items, category, gprop)
        timeline = await self._get_timeline(widgets["TIMESERIES"], geo)
        points = [
            {"timestamp": int(item["time"]), "values": item["value"][:len(keywords)]}
            for item in timeline
        ]
        return CompareResult(keywords=keywords, geo=geo, timeframe=timeframe, points=points, bytes_received=self._fetch_bytes)

    async def fetch_regions(self, keyword: str, geo: str = "", timeframe: str = "today 1-m", category: int = 0, gprop: str = "") -> list[dict]:
        """
        Interest by region: разбивка интереса по географии.
        geo="" → по странам, geo="US" → по штатам/регионам страны (как в UI Google Trends).
        """
        self._fetch_bytes = 0
        await self._warm_up()
        widgets = await self._get_tokens([{"keyword": keyword, "geo": geo, "time": timeframe}], category, gprop)
        widget = widgets.get("GEO_MAP")
        if widget is None:
            return []
        text = await self._get(
            GEO_URL,
            {
                "hl": "en-US",
                "tz": "180",
                "req": json.dumps(widget["request"], separators=(",", ":")),
                "token": widget["token"],
            },
        )
        data = json.loads(_strip_xssi(text))
        regions = []
        for item in data["default"]["geoMapData"]:
            if not item.get("hasData", [False])[0]:
                continue
            regions.append({
                "geo_code": item.get("geoCode", ""),
                "name": item.get("geoName", ""),
                "value": item["value"][0],
            })
        regions.sort(key=lambda r: r["value"], reverse=True)
        return regions

    async def fetch_related(self, keyword: str, geo: str = "", timeframe: str = "today 1-m", category: int = 0, gprop: str = "") -> dict:
        """
        Related queries: top (0–100 относительно самого популярного) и
        rising (рост в %; value может быть огромным, formatted = "+250%" или "Breakout").
        """
        self._fetch_bytes = 0
        await self._warm_up()
        widgets = await self._get_tokens([{"keyword": keyword, "geo": geo, "time": timeframe}], category, gprop)
        widget = widgets.get("RELATED_QUERIES")
        if widget is None:
            return {"top": [], "rising": []}
        text = await self._get(
            RELATED_URL,
            {
                "hl": "en-US",
                "tz": "180",
                "req": json.dumps(widget["request"], separators=(",", ":")),
                "token": widget["token"],
            },
        )
        data = json.loads(_strip_xssi(text))
        ranked = data["default"].get("rankedList", [])
        top = [
            {"query": i["query"], "value": i["value"]}
            for i in (ranked[0]["rankedKeyword"] if len(ranked) > 0 else [])
        ]
        rising = [
            {"query": i["query"], "value": i["value"], "formatted": i.get("formattedValue", "")}
            for i in (ranked[1]["rankedKeyword"] if len(ranked) > 1 else [])
        ]
        return {"top": top, "rising": rising}

    async def fetch_trending(self, geo: str, hours: int = 24) -> list[dict]:
        """
        Trending Now: трендовые запросы по гео за окно 4/24/48/168 часов.
        Внутренний batchexecute API (rpcid i0OFE), warmup и токены не нужны.
        """
        self._fetch_bytes = 0
        freq = json.dumps([[["i0OFE", json.dumps([None, None, geo, 0, "en-US", hours, 1]), None, "generic"]]])
        text = await self._request(
            "POST", TRENDING_URL,
            data={"f.req": freq},
            headers={"content-type": "application/x-www-form-urlencoded;charset=UTF-8"},
        )
        trends = []
        for line in text.split("\n"):
            line = line.strip()
            if not line.startswith('[["wrb.fr"'):
                continue
            outer = json.loads(line)
            inner = json.loads(outer[0][2])
            for t in inner[1]:
                trends.append({
                    "keyword": t[0],
                    "volume": t[6],
                    "growth_pct": t[8],   # рост в % — «arrow_upward 1,000%» в UI
                    "started_at": t[3][0] if t[3] else None,
                    "breakdown": t[9] or [],
                    "categories": t[10] or [],
                })
            break
        return trends


class ScrapePool:
    """
    Keeps one persistent AsyncSession per proxy.
    Avoids opening a new CONNECT tunnel for every job.
    Recreates session after max_requests to prevent stale state.
    On session creation optionally runs Playwright warmup to get AEC cookie.
    """

    def __init__(self, max_requests_per_session: int = 25, playwright_warmup: bool = False):
        self._scrapers: dict[str | None, GoogleTrendsScraper] = {}
        self._counts: dict[str | None, int] = {}
        self._max = max_requests_per_session
        self._playwright_warmup = playwright_warmup
        self._lock = asyncio.Lock()

    async def _new_scraper(self, proxy_url: str | None) -> "GoogleTrendsScraper":
        cookies = None
        if self._playwright_warmup:
            from app.cookie_warmer import get_google_cookies
            cookies = await get_google_cookies(proxy_url=proxy_url)
        scraper = GoogleTrendsScraper(proxy=proxy_url, cookies=cookies or None)
        await scraper.__aenter__()
        log.info("SCRAPER new session  proxy=%s  cookies=%d",
                 proxy_url or "direct", len(cookies) if cookies else 0)
        return scraper

    async def get(self, proxy_url: str | None) -> tuple["GoogleTrendsScraper", int]:
        """Returns (scraper, request_number) — request_number starts at 1."""
        async with self._lock:
            scraper = self._scrapers.get(proxy_url)
            count = self._counts.get(proxy_url, 0)
            if scraper is None or count >= self._max:
                # Don't close old scraper here — concurrent in-flight requests
                # may still be using it. Schedule async close outside the lock.
                old_scraper = scraper
                scraper = await self._new_scraper(proxy_url)
                self._scrapers[proxy_url] = scraper
                self._counts[proxy_url] = 0
                count = 0
                if old_scraper:
                    asyncio.create_task(self._close_after(old_scraper))
            self._counts[proxy_url] = count + 1
            return scraper, count + 1

    @staticmethod
    async def _close_after(scraper: "GoogleTrendsScraper", delay: float = 5.0):
        """Close a retired scraper after a short grace period for in-flight requests."""
        await asyncio.sleep(delay)
        try:
            await scraper.__aexit__(None, None, None)
        except Exception:
            pass

    async def invalidate(self, proxy_url: str | None):
        """Recreates session after critical errors."""
        async with self._lock:
            old_scraper = self._scrapers.pop(proxy_url, None)
            self._counts.pop(proxy_url, None)
            new_scraper = await self._new_scraper(proxy_url)
            self._scrapers[proxy_url] = new_scraper
            self._counts[proxy_url] = 0
            if old_scraper:
                asyncio.create_task(self._close_after(old_scraper))

    async def close(self):
        async with self._lock:
            for scraper in self._scrapers.values():
                try:
                    await scraper.__aexit__(None, None, None)
                except Exception:
                    pass
            self._scrapers.clear()
            self._counts.clear()