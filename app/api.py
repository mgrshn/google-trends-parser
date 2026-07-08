"""
FastAPI backend for Google Trends scraper.

Routes:
    GET /health         — liveness + readiness check (no auth)
    GET /trends        — single keyword, waits up to 60s for fresh data
    GET /trends/batch  — up to 20 keywords × geos, returns immediately
    GET /status        — queue health
    /proxies           — list/add/patch/delete/check proxies (for discovery admin)

Auth: X-API-Key header. Set API_KEY env var; if unset, auth is disabled (local dev).
Run: uvicorn app.api:app --reload
"""
import asyncio
import contextlib
import datetime
import os
import time
from datetime import date
from typing import Any, Literal
from urllib.parse import urlparse

from curl_cffi.requests import AsyncSession
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Security
from fastapi.security import APIKeyHeader
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .db import Database
from .job_queue import Job, JobQueue
from .scraper import IMPERSONATE

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
DB_DSN = os.getenv("DB_DSN", "postgresql://trends:trends@localhost:5432/trends")
WAIT_TIMEOUT_SEC = int(os.getenv("WAIT_TIMEOUT_SEC", "30"))
API_KEY = os.getenv("API_KEY", "")
POLL_INTERVAL_SEC = 0.5

_db: Database | None = None
_queue: JobQueue | None = None

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

PERIOD_TO_TIMEFRAME: dict[str, str] = {
    "1h":  "now 1-H",
    "4h":  "now 4-H",
    "1d":  "now 1-d",
    "7d":  "now 7-d",
    "1m":  "today 1-m",
    "3m":  "today 3-m",
    "12m": "today 12-m",
    "5y":  "today 5-y",
    "all": "all",
}

ENGINE_TO_GPROP: dict[str, str] = {
    "web":      "",
    "images":   "images",
    "news":     "news",
    "youtube":  "youtube",
    "shopping": "froogle",
}

Period = Literal["1h", "4h", "1d", "7d", "1m", "3m", "12m", "5y", "all", "custom"]


async def _require_api_key(key: str | None = Security(_api_key_header)):
    if API_KEY and key != API_KEY:
        raise HTTPException(status_code=403, detail="Invalid or missing API key")


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    global _db, _queue
    _db = Database(DB_DSN)
    await _db.connect()
    _queue = JobQueue(REDIS_URL)
    await _queue.connect()
    yield
    await _db.close()
    await _queue.close()


_DESCRIPTION = """
Scrapes Google Trends and returns normalized interest-over-time data (0–100).
Fresh data is fetched on demand and cached in PostgreSQL.

## Эндпоинты

| Метод | Путь | Auth | Описание |
|---|---|---|---|
| GET | `/trends` | ✅ | Один keyword. Ждёт результат до 60 сек, иначе 202 |
| GET | `/trends/batch` | ✅ | До 20 keywords × N стран. Отвечает сразу: кеш или `queued` |
| GET | `/trends/compare` | ✅ | 2–5 ключей в общей шкале — значения сравнимы между собой |
| GET | `/trends/regions` | ✅ | География интереса: по странам мира или регионам страны |
| GET | `/trends/related` | ✅ | Связанные запросы: top + rising (включая Breakout) |
| GET | `/trending` | ✅ | Трендовые запросы страны (Trending Now) с историей |
| GET | `/status` | ✅ | Размер очереди и количество dead jobs |
| GET | `/health` | — | Доступность БД и Redis. Для мониторинга |

**`/trends` vs `/trends/compare`:** в `/trends` каждый ключ нормализован к собственному пику (100 = пик этого ключа),
сравнивать значения разных ключей нельзя. В `/trends/compare` все ключи в одной шкале
(100 = пик самого популярного) — можно сравнивать.

**`/trending`:** трендовые запросы страны (Google Trending Now). История копится в БД при каждом
опросе: `since_hours=168` вернёт всё, что было в трендах за неделю. По каждому тренду —
объём поиска (`volume`), рост в процентах (`growth_pct`), связанные вариации (`breakdown`),
категории. Фильтры `category`/`status` и сортировки `sort=volume|growth|recency|title`.
Для массового сбора по всем гео — `scripts/enqueue_trending.py` по крону.

## Auth
Заголовок `X-API-Key`. Ключ задаётся через env-переменную `API_KEY`;
если она не задана — авторизация отключена (только для локальной разработки).
Для тестирования из Swagger UI: кнопка **Authorize** (замок вверху) → ввести ключ.

## Параметры запроса

### period
Временной диапазон. По умолчанию `1m` (последний месяц).

| Значение | Диапазон | Гранулярность точек |
|---|---|---|
| `1h` | последний час | по минутам |
| `4h` | последние 4 часа | по минутам |
| `1d` | последние 24 часа | по минутам |
| `7d` | последние 7 дней | по часам |
| `1m` | последние 30 дней | по дням |
| `3m` | последние 90 дней | по дням |
| `12m` | последние 12 месяцев | по неделям |
| `5y` | последние 5 лет | по месяцам |
| `all` | с 2004 по сегодня | по месяцам |
| `custom` | произвольный диапазон | зависит от длины |

При `period=custom` обязательны параметры `from` и `to`.

### from / to
Используются только при `period=custom`. Формат `YYYY-MM-DD`.

### geo
ISO 3166-1 alpha-2 код страны. Пустая строка — worldwide.
Примеры: `US`, `GB`, `DE`, `FR`, `RU`, `JP`, `BR`, `IN`, `AU`, `CA`

### category
ID категории Google Trends. `0` — все категории (по умолчанию).

| ID | Категория |
|---|---|
| 0 | Все категории |
| 7 | Финансы |
| 8 | Еда и напитки |
| 9 | Игры |
| 10 | Здоровье |
| 13 | Бизнес |
| 20 | Развлечения |
| 47 | Путешествия |
| 65 | Красота и фитнес |
| 77 | Электроника |
| 131 | Шопинг |
| 174 | Спорт |

### engine
| Значение | Движок |
|---|---|
| `web` | Поиск Google (по умолчанию) |
| `images` | Google Картинки |
| `news` | Google Новости |
| `youtube` | YouTube |
| `shopping` | Google Шопинг |

## Важно
Значения 0–100 **относительны**: 100 = пик интереса внутри запрошенного диапазона.
Одно и то же ключевое слово с разными диапазонами вернёт разные абсолютные значения.
"""

app = FastAPI(
    title="Google Trends API",
    description=_DESCRIPTION,
    version="1.0.0",
    lifespan=lifespan,
)

# Authenticated router — all /trends and /status routes require API key
_router = APIRouter(dependencies=[Depends(_require_api_key)])


# ── Response models ────────────────────────────────────────────────────────────

class TrendPoint(BaseModel):
    ts: str = Field(..., description="ISO-8601 timestamp (UTC)")
    value: int = Field(..., ge=0, le=100, description="Relative interest 0–100")


class TrendsResponse(BaseModel):
    keyword: str
    geo: str
    period: str
    from_date: str | None = Field(None, alias="from")
    to_date: str | None = Field(None, alias="to")
    category: int
    engine: str
    cached: bool = Field(..., description="True if data was already in DB")
    points: list[TrendPoint]

    model_config = {"populate_by_name": True}


class QueuedResponse(BaseModel):
    keyword: str
    geo: str
    period: str
    from_date: str | None = Field(None, alias="from")
    to_date: str | None = Field(None, alias="to")
    status: str = "queued"
    message: str

    model_config = {"populate_by_name": True}


class BatchKeywordResult(BaseModel):
    cached: bool | None = None
    status: str | None = None
    points: list[TrendPoint] | None = None


class BatchResponse(BaseModel):
    geos: list[str]
    period: str
    from_date: str | None = Field(None, alias="from")
    to_date: str | None = Field(None, alias="to")
    category: int
    engine: str
    results: dict[str, dict[str, BatchKeywordResult]]

    model_config = {"populate_by_name": True}


class ComparePoint(BaseModel):
    ts: str = Field(..., description="ISO-8601 timestamp (UTC)")
    values: list[int] = Field(..., description="values[i] соответствует keywords[i]")


class CompareResponse(BaseModel):
    keywords: list[str] = Field(..., description="Отсортированный список ключей — порядок values в точках")
    geo: str
    period: str
    from_date: str | None = Field(None, alias="from")
    to_date: str | None = Field(None, alias="to")
    category: int
    engine: str
    cached: bool
    points: list[ComparePoint]

    model_config = {"populate_by_name": True}


class RegionValue(BaseModel):
    geo_code: str = Field(..., description="Код региона: ISO страны (US) или подрегиона (US-CA)")
    name: str = Field(..., description="Название региона")
    value: int = Field(..., ge=0, le=100, description="Интерес 0–100 относительно самого активного региона")


class RegionsResponse(BaseModel):
    keyword: str
    geo: str
    period: str
    from_date: str | None = Field(None, alias="from")
    to_date: str | None = Field(None, alias="to")
    category: int
    engine: str
    cached: bool
    regions: list[RegionValue]

    model_config = {"populate_by_name": True}


class RelatedQuery(BaseModel):
    query: str
    value: int = Field(..., description="top: 0–100 относительно самого популярного; rising: рост в %")
    formatted: str | None = Field(None, description="Только rising: '+250%' или 'Breakout' (рост > 5000%)")


class RelatedResponse(BaseModel):
    keyword: str
    geo: str
    period: str
    from_date: str | None = Field(None, alias="from")
    to_date: str | None = Field(None, alias="to")
    category: int
    engine: str
    cached: bool
    top: list[RelatedQuery] = Field(..., description="Самые популярные связанные запросы")
    rising: list[RelatedQuery] = Field(..., description="Быстрорастущие связанные запросы")

    model_config = {"populate_by_name": True}


class TrendingItem(BaseModel):
    keyword: str
    volume: int | None = Field(None, description="Примерный объём поиска (100, 1000, 500000...)")
    growth_pct: int | None = Field(None, description="Рост в % («1000» = +1,000% в UI Google)")
    started_at: str = Field(..., description="Когда тренд начался (по данным Google)")
    breakdown: list[str] = Field(..., description="Связанные вариации запроса")
    categories: list[int] = Field(..., description="ID категорий Google")
    first_seen_at: str = Field(..., description="Когда мы впервые увидели тренд")


class TrendingResponse(BaseModel):
    geo: str
    since_hours: int
    cached: bool
    count: int
    trends: list[TrendingItem]


class StatusResponse(BaseModel):
    queue: int = Field(..., description="Jobs waiting to be processed")
    dead: int = Field(..., description="Jobs that failed all retry attempts")


class HealthResponse(BaseModel):
    status: str
    db: str
    redis: str


class ProxyOut(BaseModel):
    id: int
    host: str = Field(..., description="host:port (без учётных данных)")
    protocol: str = Field(..., description="http / https / socks5")
    username: str | None = Field(None, description="Логин (пароль не возвращается)")
    enabled: bool
    success_count: int
    fail_count: int
    added_at: str | None = None
    last_used_at: str | None = None


class ProxyCreate(BaseModel):
    url: str = Field(..., example="http://user:pass@host:port", description="Полный URL прокси")


class ProxyPatch(BaseModel):
    enabled: bool = Field(..., description="Включить/выключить прокси")


class ProxyCheckResult(BaseModel):
    ok: bool
    latency_ms: int | None = None
    exit_ip: str | None = None
    error: str | None = None


# ── Helpers ────────────────────────────────────────────────────────────────────

def _proxy_public(row: dict) -> dict:
    """Разбирает url прокси в безопасное представление (без пароля)."""
    p = urlparse(row["url"])
    added = row.get("added_at")
    used = row.get("last_used_at")
    return {
        "id": row["id"],
        "host": f"{p.hostname}:{p.port}" if p.port else (p.hostname or row["url"]),
        "protocol": p.scheme or "http",
        "username": p.username,
        "enabled": row["enabled"],
        "success_count": row.get("success_count", 0),
        "fail_count": row.get("fail_count", 0),
        "added_at": added.isoformat() if added else None,
        "last_used_at": used.isoformat() if used else None,
    }


async def _check_proxy(url: str, timeout: float = 10.0) -> dict:
    """Живой тест прокси: тянет внешний IP через него, меряет задержку."""
    start = time.monotonic()
    try:
        async with AsyncSession(impersonate=IMPERSONATE,
                                proxies={"https": url, "http": url}, timeout=timeout) as s:
            r = await s.get("https://ifconfig.me/ip")
            latency = int((time.monotonic() - start) * 1000)
            if r.status_code == 200:
                return {"ok": True, "latency_ms": latency, "exit_ip": r.text.strip(), "error": None}
            return {"ok": False, "latency_ms": latency, "exit_ip": None, "error": f"HTTP {r.status_code}"}
    except Exception as e:
        return {"ok": False, "latency_ms": None, "exit_ip": None, "error": str(e)[:200]}

def _resolve_timeframe(period: str, from_date: date | None, to_date: date | None) -> tuple[str, str | None, str | None]:
    if period != "custom":
        return PERIOD_TO_TIMEFRAME[period], None, None
    if from_date is None or to_date is None:
        raise HTTPException(400, "from and to are required when period=custom")
    if from_date > to_date:
        raise HTTPException(400, "from must be before to")
    return f"{from_date} {to_date}", str(from_date), str(to_date)


async def _enqueue_and_wait(keyword: str, geo: str, timeframe: str, category: int, gprop: str) -> list[dict] | None:
    await _queue.push_unique([Job(keyword=keyword, geo=geo, timeframe=timeframe, category=category, gprop=gprop)])
    deadline = asyncio.get_running_loop().time() + WAIT_TIMEOUT_SEC
    while asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(POLL_INTERVAL_SEC)
        points, _ = await _db.get_points(keyword, geo, timeframe, category, gprop)
        # [] — валидный ответ: Google подтвердил что данных нет (слишком мало поисков)
        if points is not None:
            return points
    return None


async def _enqueue_compare_and_wait(keywords: list[str], geo: str, timeframe: str, category: int, gprop: str) -> list[dict] | None:
    job = Job(keyword=",".join(keywords), geo=geo, timeframe=timeframe, category=category, gprop=gprop, kind="compare")
    await _queue.push_unique([job])
    deadline = asyncio.get_running_loop().time() + WAIT_TIMEOUT_SEC
    while asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(POLL_INTERVAL_SEC)
        points, _ = await _db.get_compare(keywords, geo, timeframe, category, gprop)
        if points is not None:
            return points
    return None


async def _enqueue_widget_and_wait(kind: str, keyword: str, geo: str, timeframe: str, category: int, gprop: str):
    job = Job(keyword=keyword, geo=geo, timeframe=timeframe, category=category, gprop=gprop, kind=kind)
    await _queue.push_unique([job])
    deadline = asyncio.get_running_loop().time() + WAIT_TIMEOUT_SEC
    while asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(POLL_INTERVAL_SEC)
        data, _ = await _db.get_widget(kind, keyword, geo, timeframe, category, gprop)
        if data is not None:
            return data
    return None


TRENDING_TTL_MINUTES = 60


async def _enqueue_trending_and_wait(geo: str, hours: int) -> bool:
    """Ставит job на опрос трендов и ждёт свежих данных. True = данные обновились."""
    started = datetime.datetime.now(tz=datetime.timezone.utc)
    job = Job(keyword="", geo=geo, timeframe=str(hours), kind="trending")
    await _queue.push_unique([job])
    deadline = asyncio.get_running_loop().time() + WAIT_TIMEOUT_SEC
    while asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(POLL_INTERVAL_SEC)
        last = await _db.trending_last_poll(geo)
        if last and last >= started:
            return True
    return False


# ── System routes (no auth) ────────────────────────────────────────────────────

@app.get(
    "/health",
    response_model=HealthResponse,
    summary="Liveness and readiness check",
    tags=["system"],
)
async def health():
    """Checks DB and Redis connectivity. Returns 503 if either is unavailable."""
    checks: dict[str, str] = {}

    try:
        await _db._pool.fetchval("SELECT 1")
        checks["db"] = "ok"
    except Exception:
        checks["db"] = "error"

    try:
        await _queue._r.ping()
        checks["redis"] = "ok"
    except Exception:
        checks["redis"] = "error"

    status = "ok" if all(v == "ok" for v in checks.values()) else "degraded"
    code = 200 if status == "ok" else 503
    return JSONResponse(status_code=code, content={"status": status, **checks})


# ── Authenticated routes ───────────────────────────────────────────────────────

@_router.get(
    "/trends",
    response_model=TrendsResponse,
    responses={202: {"model": QueuedResponse, "description": "Job enqueued, retry later"}},
    summary="Get trend data for a single keyword",
    tags=["trends"],
)
async def get_trends(
    keyword: str = Query(..., example="bitcoin", description="Поисковый запрос"),
    geo: str = Query("", example="US", description="Страна ISO 3166-1 alpha-2. Пусто = worldwide"),
    period: Period = Query("1m", description="Временной диапазон. `custom` требует from и to"),
    from_date: date | None = Query(None, alias="from", example="2025-01-01", description="Начало диапазона YYYY-MM-DD (только при period=custom)"),
    to_date: date | None = Query(None, alias="to", example="2025-12-31", description="Конец диапазона YYYY-MM-DD (только при period=custom)"),
    category: int = Query(0, example=7, description="ID категории (0 = все; 7 = финансы; 20 = развлечения; 47 = путешествия)"),
    engine: Literal["web", "images", "news", "youtube", "shopping"] = Query("web", description="Движок: web / images / news / youtube / shopping"),
):
    """
    Returns normalized interest-over-time (0–100) for the given keyword.

    - If fresh data exists in the database, returns it immediately (`cached: true`).
    - If data is missing or stale, enqueues a scrape job and waits up to 60 seconds.
    - If the worker doesn't finish in time, returns **202 Accepted** — retry later.
    """
    timeframe, from_str, to_str = _resolve_timeframe(period, from_date, to_date)
    gprop = ENGINE_TO_GPROP[engine]

    points, _ = await _db.get_points(keyword, geo, timeframe, category, gprop)
    if points is not None:
        return {"keyword": keyword, "geo": geo, "period": period, "from": from_str, "to": to_str,
                "category": category, "engine": engine, "cached": True, "points": points}

    points = await _enqueue_and_wait(keyword, geo, timeframe, category, gprop)
    if points is None:
        points = await _enqueue_and_wait(keyword, geo, timeframe, category, gprop)
    if points is not None:
        return {"keyword": keyword, "geo": geo, "period": period, "from": from_str, "to": to_str,
                "category": category, "engine": engine, "cached": False, "points": points}

    return JSONResponse(status_code=202, content={
        "keyword": keyword, "geo": geo, "period": period, "from": from_str, "to": to_str,
        "status": "queued",
        "message": f"Data is being collected. Retry in {WAIT_TIMEOUT_SEC}s.",
    })


@_router.get(
    "/trends/batch",
    response_model=BatchResponse,
    summary="Get trend data for multiple keywords",
    tags=["trends"],
)
async def get_trends_batch(
    keywords: str = Query(..., example="bitcoin,ethereum,solana", description="Ключевые слова через запятую (макс. 20)"),
    geos: str = Query("", example="US,GB", description="Страны через запятую. Пусто = worldwide"),
    period: Period = Query("1m", description="Временной диапазон. `custom` требует from и to"),
    from_date: date | None = Query(None, alias="from", example="2025-01-01", description="Начало диапазона YYYY-MM-DD (только при period=custom)"),
    to_date: date | None = Query(None, alias="to", example="2025-12-31", description="Конец диапазона YYYY-MM-DD (только при period=custom)"),
    category: int = Query(0, example=7, description="ID категории (0 = все; 7 = финансы; 20 = развлечения; 47 = путешествия)"),
    engine: Literal["web", "images", "news", "youtube", "shopping"] = Query("web", description="Движок: web / images / news / youtube / shopping"),
):
    """
    Returns cached data for all requested keywords × geos immediately (no waiting).

    Cached combinations are returned with `cached: true`.
    Missing ones are enqueued and returned with `status: queued` —
    poll `/trends?keyword=...&geo=...` for each individually.
    """
    kw_list = [k.strip() for k in keywords.split(",") if k.strip()]
    if len(kw_list) > 20:
        raise HTTPException(400, "Max 20 keywords per batch request")

    timeframe, from_str, to_str = _resolve_timeframe(period, from_date, to_date)
    gprop = ENGINE_TO_GPROP[engine]
    geo_list = [g.strip() for g in geos.split(",") if g.strip()] or [""]
    pairs = [(kw, geo) for kw in kw_list for geo in geo_list]

    fetched = await asyncio.gather(*[_db.get_points(kw, geo, timeframe, category, gprop) for kw, geo in pairs])

    results: dict[str, Any] = {}
    to_enqueue = []

    for (kw, geo), (points, _) in zip(pairs, fetched):
        kw_entry = results.setdefault(kw, {})
        if points is not None:
            kw_entry[geo] = {"cached": True, "points": points}
        else:
            kw_entry[geo] = {"cached": False, "status": "queued"}
            to_enqueue.append(Job(keyword=kw, geo=geo, timeframe=timeframe, category=category, gprop=gprop))

    if to_enqueue:
        await _queue.push_unique(to_enqueue)

    return {"geos": geo_list, "period": period, "from": from_str, "to": to_str,
            "category": category, "engine": engine, "results": results}


@_router.get(
    "/trends/compare",
    response_model=CompareResponse,
    responses={202: {"description": "Job enqueued, retry later"}},
    summary="Compare 2–5 keywords in a shared scale",
    tags=["trends"],
)
async def get_trends_compare(
    keywords: str = Query(..., example="bitcoin,trump", description="2–5 ключей через запятую"),
    geo: str = Query("", example="US", description="Страна ISO 3166-1 alpha-2. Пусто = worldwide"),
    period: Period = Query("1m", description="Временной диапазон. `custom` требует from и to"),
    from_date: date | None = Query(None, alias="from", example="2025-01-01", description="Начало диапазона YYYY-MM-DD (только при period=custom)"),
    to_date: date | None = Query(None, alias="to", example="2025-12-31", description="Конец диапазона YYYY-MM-DD (только при period=custom)"),
    category: int = Query(0, example=0, description="ID категории (0 = все)"),
    engine: Literal["web", "images", "news", "youtube", "shopping"] = Query("web", description="Движок: web / images / news / youtube / shopping"),
):
    """
    Сравнение ключей в **общей шкале**: 100 = пик самого популярного ключа в выборке.
    В отличие от `/trends`, значения разных ключей здесь сравнимы между собой.

    `keywords` в ответе отсортированы по алфавиту; `points[].values[i]` соответствует `keywords[i]`.
    Одна и та же комбинация ключей кешируется независимо от порядка в запросе.
    """
    kw_list = sorted({k.strip() for k in keywords.split(",") if k.strip()})
    if not 2 <= len(kw_list) <= 5:
        raise HTTPException(400, "compare requires 2 to 5 distinct keywords")

    timeframe, from_str, to_str = _resolve_timeframe(period, from_date, to_date)
    gprop = ENGINE_TO_GPROP[engine]

    points, _ = await _db.get_compare(kw_list, geo, timeframe, category, gprop)
    if points is not None:
        return {"keywords": kw_list, "geo": geo, "period": period, "from": from_str, "to": to_str,
                "category": category, "engine": engine, "cached": True, "points": points}

    points = await _enqueue_compare_and_wait(kw_list, geo, timeframe, category, gprop)
    if points is not None:
        return {"keywords": kw_list, "geo": geo, "period": period, "from": from_str, "to": to_str,
                "category": category, "engine": engine, "cached": False, "points": points}

    return JSONResponse(status_code=202, content={
        "keywords": kw_list, "geo": geo, "period": period, "from": from_str, "to": to_str,
        "status": "queued",
        "message": f"Data is being collected. Retry in {WAIT_TIMEOUT_SEC}s.",
    })


@_router.get(
    "/trends/regions",
    response_model=RegionsResponse,
    responses={202: {"description": "Job enqueued, retry later"}},
    summary="Interest by region",
    tags=["trends"],
)
async def get_trends_regions(
    keyword: str = Query(..., example="bitcoin", description="Поисковый запрос"),
    geo: str = Query("", example="US", description="Пусто = разбивка по странам мира; код страны = по её регионам"),
    period: Period = Query("1m", description="Временной диапазон. `custom` требует from и to"),
    from_date: date | None = Query(None, alias="from", example="2025-01-01", description="Начало диапазона YYYY-MM-DD (только при period=custom)"),
    to_date: date | None = Query(None, alias="to", example="2025-12-31", description="Конец диапазона YYYY-MM-DD (только при period=custom)"),
    category: int = Query(0, example=0, description="ID категории (0 = все)"),
    engine: Literal["web", "images", "news", "youtube", "shopping"] = Query("web", description="Движок: web / images / news / youtube / shopping"),
):
    """
    География интереса: где ключ ищут больше всего.
    `value` 0–100 относительно самого активного региона (100 = лидер).
    `geo` пустой — список стран; `geo=US` — штаты США, и т.д. Регионы без данных не включаются.
    """
    timeframe, from_str, to_str = _resolve_timeframe(period, from_date, to_date)
    gprop = ENGINE_TO_GPROP[engine]

    data, _ = await _db.get_widget("regions", keyword, geo, timeframe, category, gprop)
    cached = data is not None
    if data is None:
        data = await _enqueue_widget_and_wait("regions", keyword, geo, timeframe, category, gprop)
    if data is not None:
        return {"keyword": keyword, "geo": geo, "period": period, "from": from_str, "to": to_str,
                "category": category, "engine": engine, "cached": cached, "regions": data}

    return JSONResponse(status_code=202, content={
        "keyword": keyword, "geo": geo, "period": period, "from": from_str, "to": to_str,
        "status": "queued",
        "message": f"Data is being collected. Retry in {WAIT_TIMEOUT_SEC}s.",
    })


@_router.get(
    "/trends/related",
    response_model=RelatedResponse,
    responses={202: {"description": "Job enqueued, retry later"}},
    summary="Related queries (top + rising)",
    tags=["trends"],
)
async def get_trends_related(
    keyword: str = Query(..., example="bitcoin", description="Поисковый запрос"),
    geo: str = Query("", example="US", description="Страна ISO 3166-1 alpha-2. Пусто = worldwide"),
    period: Period = Query("1m", description="Временной диапазон. `custom` требует from и to"),
    from_date: date | None = Query(None, alias="from", example="2025-01-01", description="Начало диапазона YYYY-MM-DD (только при period=custom)"),
    to_date: date | None = Query(None, alias="to", example="2025-12-31", description="Конец диапазона YYYY-MM-DD (только при period=custom)"),
    category: int = Query(0, example=0, description="ID категории (0 = все)"),
    engine: Literal["web", "images", "news", "youtube", "shopping"] = Query("web", description="Движок: web / images / news / youtube / shopping"),
):
    """
    Связанные запросы — что ещё ищут люди, интересующиеся ключом.

    - `top` — самые популярные, value 0–100 относительно первого
    - `rising` — быстрорастущие, value = рост в %; `formatted` = "+250%" или "Breakout" (рост >5000%)

    Rising — сырьё для discovery: там появляются новые темы до того, как станут заметными.
    """
    timeframe, from_str, to_str = _resolve_timeframe(period, from_date, to_date)
    gprop = ENGINE_TO_GPROP[engine]

    data, _ = await _db.get_widget("related", keyword, geo, timeframe, category, gprop)
    cached = data is not None
    if data is None:
        data = await _enqueue_widget_and_wait("related", keyword, geo, timeframe, category, gprop)
    if data is not None:
        return {"keyword": keyword, "geo": geo, "period": period, "from": from_str, "to": to_str,
                "category": category, "engine": engine, "cached": cached,
                "top": data["top"], "rising": data["rising"]}

    return JSONResponse(status_code=202, content={
        "keyword": keyword, "geo": geo, "period": period, "from": from_str, "to": to_str,
        "status": "queued",
        "message": f"Data is being collected. Retry in {WAIT_TIMEOUT_SEC}s.",
    })


@_router.get(
    "/trending",
    response_model=TrendingResponse,
    responses={202: {"description": "Job enqueued, retry later"}},
    summary="Trending searches by geo",
    tags=["trends"],
)
async def get_trending(
    geo: str = Query(..., example="US", description="Страна ISO 3166-1 alpha-2 (обязательна)"),
    since_hours: int = Query(24, ge=1, le=168, description="За сколько последних часов вернуть тренды"),
    limit: int = Query(100, ge=1, le=500, description="Максимум трендов в ответе"),
    category: int | None = Query(None, example=17, description="Фильтр по ID категории Google (17 = спорт)"),
    sort: Literal["volume", "growth", "recency", "title"] = Query("volume", description="Сортировка: volume / growth / recency / title"),
    status: Literal["all", "active"] = Query("all", description="active = только тренды из самого свежего опроса"),
):
    """
    Трендовые запросы страны (Google Trending Now) с накоплением истории.

    - `volume` — примерный объём поиска, `growth_pct` — рост в %
    - `breakdown` — связанные вариации запроса (расширяют словарь ключей)
    - Данные считаются свежими 60 минут; протухли — обновляем на лету
    - История копится в БД: `since_hours=168` вернёт всё за неделю, что мы видели
    """
    last = await _db.trending_last_poll(geo)
    ttl_cutoff = datetime.datetime.now(tz=datetime.timezone.utc) - datetime.timedelta(minutes=TRENDING_TTL_MINUTES)
    fresh = last is not None and last >= ttl_cutoff

    cached = True
    if not fresh:
        updated = await _enqueue_trending_and_wait(geo, hours=24)
        cached = not updated

    trends = await _db.get_trending(geo, since_hours, limit, category=category, sort=sort,
                                    active_only=(status == "active"))
    if not trends and not fresh and cached:
        return JSONResponse(status_code=202, content={
            "geo": geo, "status": "queued",
            "message": f"Trending data is being collected. Retry in {WAIT_TIMEOUT_SEC}s.",
        })

    return {"geo": geo, "since_hours": since_hours, "cached": cached, "count": len(trends), "trends": trends}


@_router.get(
    "/status",
    response_model=StatusResponse,
    summary="Queue and worker health",
    tags=["system"],
)
async def status():
    """Returns the number of pending jobs and permanently failed jobs."""
    return {"queue": await _queue.size(), "dead": await _queue.dead_size()}


@_router.get(
    "/proxies",
    response_model=list[ProxyOut],
    summary="List proxies",
    tags=["proxies"],
)
async def list_proxies():
    """Текущие прокси-серверы парсера. Пароль в ответе не отдаётся."""
    rows = await _db.list_proxies()
    return [_proxy_public(r) for r in rows]


@_router.post(
    "/proxies",
    response_model=ProxyOut,
    status_code=201,
    summary="Add proxy",
    tags=["proxies"],
)
async def add_proxy(body: ProxyCreate):
    """Добавить прокси по URL. Если такой url уже есть — включает его."""
    if "://" not in body.url or "@" not in body.url and not urlparse(body.url).hostname:
        raise HTTPException(400, "url должен быть вида http://user:pass@host:port")
    row = await _db.add_proxy(body.url)
    return _proxy_public(row)


@_router.patch(
    "/proxies/{proxy_id}",
    response_model=ProxyOut,
    summary="Enable/disable proxy",
    tags=["proxies"],
)
async def patch_proxy(proxy_id: int, body: ProxyPatch):
    """Включить или выключить прокси. Воркер подхватит изменение по SIGHUP."""
    if not await _db.set_proxy_enabled(proxy_id, body.enabled):
        raise HTTPException(404, "Proxy not found")
    rows = await _db.list_proxies()
    row = next((r for r in rows if r["id"] == proxy_id), None)
    return _proxy_public(row)


@_router.delete(
    "/proxies/{proxy_id}",
    status_code=204,
    summary="Delete proxy",
    tags=["proxies"],
)
async def delete_proxy(proxy_id: int):
    """Удалить прокси. Воркер подхватит изменение по SIGHUP."""
    if not await _db.delete_proxy(proxy_id):
        raise HTTPException(404, "Proxy not found")
    return JSONResponse(status_code=204, content=None)


@_router.post(
    "/proxies/{proxy_id}/check",
    response_model=ProxyCheckResult,
    summary="Test a proxy",
    tags=["proxies"],
)
async def check_proxy(proxy_id: int):
    """Живой тест: тянет внешний IP через прокси, возвращает ok/latency/exit_ip/error."""
    proxy = await _db.get_proxy(proxy_id)
    if not proxy:
        raise HTTPException(404, "Proxy not found")
    return await _check_proxy(proxy["url"])


app.include_router(_router)