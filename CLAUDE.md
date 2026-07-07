# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Neighbor service: trends-discovery

A discovery product (Exploding Topics clone) lives at `/Users/mgr/Projects/SMH/trends-discovery`
and consumes this parser **via HTTP API only**. It is maintained by a separate agent.
Its feature requests to this parser land in `/Users/mgr/Projects/SMH/trends-discovery/PARSER_REQUESTS.md` —
check it when the user asks, implement, and move done items to the «Выполнено» section there.
This parser stays headless and product-agnostic: no discovery logic, no users/projects here.

## Project Overview

A Google Trends scraping service. Clients request keyword trend data via a FastAPI HTTP API; missing data is enqueued to Redis; workers pull jobs, scrape Google Trends through rotating proxies, and persist results to PostgreSQL. Results are served from cache on subsequent requests.

API surface: `/trends` (time series per keyword), `/trends/batch` (up to 20 keywords × N geos),
`/trends/compare` (2–5 keywords in a shared scale), `/trends/regions` (interest by region),
`/trends/related` (related queries: top + rising), `/trending` (Trending Now per geo with history),
`/status`, `/health` (no auth).

## Tech Stack

- **Python 3.11+** with `uv` for dependency management
- **FastAPI** + `asyncpg` (async PostgreSQL) + `redis.asyncio`
- **curl_cffi** for HTTP scraping (TLS fingerprint impersonation as Chrome)
- **Docker Compose** for local infra (PostgreSQL 16, Redis 7)

## Commands

```bash
# Install dependencies
uv sync

# Run API locally (requires postgres + redis running)
uvicorn app.api:app --reload

# Run worker locally
python worker.py --concurrency 10

# Apply migrations
python migrations/run.py

# Start all infrastructure
docker compose up -d

# Apply migrations inside Docker
docker compose exec api python migrations/run.py

# Proxy management
python scripts/proxy.py add http://user:pass@host:port
python scripts/proxy.py list
python scripts/proxy.py enable 2 / disable 2 / remove 2

# Reload proxy list in running worker (no restart)
docker compose kill -s HUP worker

# Enqueue keywords
python scripts/enqueue.py --file tests/keywords.txt --geo US,GB

# Enqueue trending-jobs for all geos (cron every 4h)
python scripts/enqueue_trending.py                # all ~65 geos, 24h window
python scripts/enqueue_trending.py --geos US,DE --hours 168

# Cleanup old data (dry-run first)
python scripts/cleanup.py --dry-run
python scripts/cleanup.py    # --stale-days 7 --partition-months 12 --trending-days 90 --dead-limit 1000

# Benchmark
python scripts/run_bench.py logs/run1 600 4      # 4 proxies, 600s
python scripts/run_dch_only.py logs/run1 600 24  # 1 proxy, 24 concurrency
```

## Architecture

```
Client → FastAPI (app/api.py)
              │
              ├─ cache hit → return immediately
              └─ miss → Redis Queue (trends:queue)
                               │
                          Worker (worker.py)
                               │
                          ProxyPool (app/proxy_pool.py)
                               │
                          ScrapePool → GoogleTrendsScraper (app/scraper.py)
                               │
                     Google Trends API (/explore → /widgetdata/multiline)
                               │
                          PostgreSQL (app/db.py)
```

### Key design details

**Scraper** (`app/scraper.py`): `GoogleTrendsScraper` holds one persistent `curl_cffi` `AsyncSession` per proxy. Two requests per keyword: `/explore` to get widget tokens, then a widget endpoint: `/widgetdata/multiline` (time series), `/widgetdata/comparedgeo` (regions), `/widgetdata/relatedsearches` (related queries). XSSI prefix (`)]}'`) is stripped before JSON parsing. `fetch_trending` is different: a single POST to `/_/TrendsUi/data/batchexecute` (rpcid `i0OFE`), no warmup/tokens needed; window 4/24/48/168 hours. `ScrapePool` manages a dict of scrapers keyed by proxy URL, rotating sessions after `max_requests` (default 25) to prevent stale cookies/state.

**Proxy pool** (`app/proxy_pool.py`): Proxies are loaded from the `proxies` DB table. On 429, a proxy enters cooldown: 5s in `--rotating` mode, 120s for static proxies. Proxy list refreshes on `SIGHUP` without worker restart.

**Job queue** (`app/job_queue.py`): Redis list `trends:queue` (LPUSH/BRPOP). `Job.kind` selects the scrape type: `series` (default) / `compare` / `regions` / `related` / `trending`; for compare jobs `keyword` holds the sorted comma-joined keyword list, for trending jobs `timeframe` holds the hours window. `push_unique` dedups via `trends:inflight:*` SETNX keys (TTL 300s); the worker clears the key on success or when the job goes dead. Failed jobs after 3 attempts go to `trends:dead`.

**Database** (`app/db.py`): `trend_points` is range-partitioned by `point_ts` (monthly partitions from 2024-01, plus a DEFAULT partition for out-of-range dates). Partitions are auto-created on startup up to 3 months ahead. Cache freshness is per-timeframe: 1 minute for `1h` data, up to 30 days for `5y`/`all`. `get_points` returns `None` when scraping is needed and `[]` when Google confirmed there is no data (valid cached answer — API checks `is not None`). Whole-result caches are JSONB: `compare_cache` (keyed by sorted keywords array) and `widget_cache` (keyed by kind + keyword params). `trending_searches` accumulates history with upsert on `(geo, keyword, started_at)` — volume/growth take GREATEST, `last_seen_at` bumps on re-poll.

**API** (`app/api.py`): `GET /trends` enqueues the job and polls DB every 0.5s for up to `WAIT_TIMEOUT_SEC` (default 30s), twice, returning 202 if the worker doesn't respond in time. `GET /trends/batch` returns immediately — cached results inline, uncached ones as `status: queued`. `/trending` freshness TTL is 60 min, refreshes on demand, serves stale data as fallback; supports `sort` (volume/growth/recency/title), `category`, `status=active` — Google's own UI does these filters client-side, we do them in SQL. Auth via `X-API-Key` header on all routes except `/health`; disabled if `API_KEY` env var is unset.

**Worker** (`worker.py`): Asyncio-based, bounded by `asyncio.Semaphore(concurrency)`, branches on `job.kind`. Handles `SIGINT`/`SIGTERM` for graceful drain, `SIGHUP` for proxy reload. Optional Playwright warmup mode (`--playwright`) fetches real Google cookies via browser for static DC proxies.

## Database Schema

```
keywords          (id, keyword UNIQUE, created_at)
trend_series      (id, keyword_id, geo, timeframe, resolution, category, gprop,
                   last_scraped_at, last_accessed_at)
                  UNIQUE (keyword_id, geo, timeframe, category, gprop)
trend_points      (series_id, point_ts, value) PARTITION BY RANGE (point_ts)
proxies           (id, url, enabled, added_at, success_count, fail_count)
compare_cache     (keywords TEXT[], geo, timeframe, category, gprop, data JSONB,
                   last_scraped_at, last_accessed_at)  -- /trends/compare, whole-result cache
widget_cache      (kind, keyword, geo, timeframe, category, gprop, data JSONB,
                   last_scraped_at, last_accessed_at)  -- regions/related, whole-result cache
trending_searches (geo, keyword, volume, growth_pct, started_at, breakdown JSONB,
                   categories INT[], first_seen_at, last_seen_at)
                  UNIQUE (geo, keyword, started_at)    -- Trending Now history
```

Migrations are numbered SQL files in `migrations/`. Run `migrations/run.py` to apply pending ones (tracks applied migrations in a `schema_migrations` table).

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `DB_DSN` | `postgresql://trends:trends@localhost:5432/trends` | PostgreSQL connection |
| `REDIS_URL` | `redis://localhost:6379` | Redis connection |
| `API_KEY` | _(unset)_ | Auth key for `X-API-Key`; unset = auth disabled |
| `WAIT_TIMEOUT_SEC` | `30` | How long `/trends` waits for worker |
| `WORKER_CONCURRENCY` | `24` | Async tasks per worker process |
| `WORKER_REPLICAS` | `1` | Docker worker replica count |
| `UVICORN_WORKERS` | `2` | API process count |
| `LOG_LEVEL` | `error` | Python logging level |