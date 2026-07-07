import datetime
import json
import logging

import asyncpg

log = logging.getLogger(__name__)

DSN = "postgresql://trends:trends@localhost:5432/trends"

TIMEFRAME_RESOLUTION = {
    "now 1-H":    "MINUTE",
    "now 4-H":    "MINUTE",
    "now 1-d":    "MINUTE",
    "now 7-d":    "HOUR",
    "today 1-m":  "DAY",
    "today 3-m":  "DAY",
    "today 12-m": "WEEK",
    "today 5-y":  "MONTH",
    "all":        "MONTH",
}

# Через сколько минут данные считаются устаревшими (совпадает с частотой обновления Google)
TIMEFRAME_TTL_MINUTES = {
    "now 1-H":    1,
    "now 4-H":    8,
    "now 1-d":    16,
    "now 7-d":    240,       # 4 часа
    "today 1-m":  1440,      # 24 часа
    "today 3-m":  1440,
    "today 12-m": 10080,     # 7 дней
    "today 5-y":  43200,     # 30 дней
    "all":        43200,
}



def _add_months(d: datetime.date, months: int) -> datetime.date:
    month = d.month - 1 + months
    year = d.year + month // 12
    month = month % 12 + 1
    return d.replace(year=year, month=month, day=1)


class Database:
    def __init__(self, dsn: str = DSN):
        self._dsn = dsn
        self._pool: asyncpg.Pool | None = None

    async def connect(self):
        self._pool = await asyncpg.create_pool(self._dsn, min_size=2, max_size=10)
        await self._ensure_partitions()

    async def _ensure_partitions(self, months_ahead: int = 3):
        """Создаёт месячные партиции от 2024-01 до текущего месяца + months_ahead."""
        async with self._pool.acquire() as conn:
            existing = await conn.fetch("""
                SELECT child.relname
                FROM pg_inherits
                JOIN pg_class parent ON pg_inherits.inhparent = parent.oid
                JOIN pg_class child  ON pg_inherits.inhrelid  = child.oid
                WHERE parent.relname = 'trend_points'
            """)
            existing_names = {r["relname"] for r in existing}

            today = datetime.date.today()
            end = _add_months(today.replace(day=1), months_ahead)
            current = datetime.date(2024, 1, 1)
            created = 0

            while current <= end:
                name = f"trend_points_{current.year}_{current.month:02d}"
                if name not in existing_names:
                    next_month = _add_months(current, 1)
                    await conn.execute(f"""
                        CREATE TABLE IF NOT EXISTS {name}
                        PARTITION OF trend_points
                        FOR VALUES FROM ('{current}') TO ('{next_month}')
                    """)
                    created += 1
                current = _add_months(current, 1)

            if created:
                log.info("Created %d new partition(s) for trend_points", created)

    async def close(self):
        if self._pool:
            await self._pool.close()

    async def save_points(self, keyword: str, geo: str, timeframe: str, points: list[dict], category: int = 0, gprop: str = ""):
        """
        points: [{"ts": datetime, "value": int}]
        Upserts keyword → series → points, updates last_scraped_at.
        """
        resolution = TIMEFRAME_RESOLUTION.get(timeframe, "DAY")

        async with self._pool.acquire() as conn:
            async with conn.transaction():
                kw_id = await conn.fetchval(
                    """
                    INSERT INTO keywords (keyword) VALUES ($1)
                    ON CONFLICT (keyword) DO UPDATE SET keyword = EXCLUDED.keyword
                    RETURNING id
                    """,
                    keyword,
                )

                series_id = await conn.fetchval(
                    """
                    INSERT INTO trend_series (keyword_id, geo, timeframe, resolution, category, gprop, last_scraped_at)
                    VALUES ($1, $2, $3, $4, $5, $6, now())
                    ON CONFLICT ON CONSTRAINT trend_series_unique DO UPDATE
                        SET last_scraped_at = now()
                    RETURNING id
                    """,
                    kw_id, geo, timeframe, resolution, category, gprop,
                )

                rows = [(series_id, p["ts"], p["value"]) for p in points]
                await conn.executemany(
                    """
                    INSERT INTO trend_points (series_id, point_ts, value)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (series_id, point_ts) DO UPDATE SET value = EXCLUDED.value
                    """,
                    rows,
                )

    async def get_points(self, keyword: str, geo: str, timeframe: str, category: int = 0, gprop: str = ""):
        """Returns (points, last_scraped_at) if fresh data exists, else (None, None)."""
        ttl_minutes = TIMEFRAME_TTL_MINUTES.get(timeframe, 1440)
        async with self._pool.acquire() as conn:
            series = await conn.fetchrow(
                """
                SELECT s.id, s.last_scraped_at
                FROM trend_series s
                JOIN keywords k ON k.id = s.keyword_id
                WHERE k.keyword = $1 AND s.geo = $2 AND s.timeframe = $3
                  AND s.category = $4 AND s.gprop = $5
                """,
                keyword, geo, timeframe, category, gprop,
            )
            if not series:
                return None, None

            cutoff = datetime.datetime.now(tz=datetime.timezone.utc) - datetime.timedelta(minutes=ttl_minutes)
            if series["last_scraped_at"] < cutoff:
                return None, series["last_scraped_at"]

            await conn.execute(
                "UPDATE trend_series SET last_accessed_at = now() WHERE id = $1",
                series["id"],
            )
            rows = await conn.fetch(
                "SELECT point_ts, value FROM trend_points WHERE series_id = $1 ORDER BY point_ts",
                series["id"],
            )
            points = [{"ts": r["point_ts"].isoformat(), "value": r["value"]} for r in rows]
            return points, series["last_scraped_at"]

    async def save_compare(self, keywords: list[str], geo: str, timeframe: str, points: list[dict], category: int = 0, gprop: str = ""):
        """
        points: [{"ts": iso-str, "values": [int, ...]}] — values[i] соответствует keywords[i].
        keywords должны быть отсортированы (канонический ключ кеша).
        """
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO compare_cache (keywords, geo, timeframe, category, gprop, data, last_scraped_at)
                VALUES ($1, $2, $3, $4, $5, $6::jsonb, now())
                ON CONFLICT ON CONSTRAINT compare_cache_unique DO UPDATE
                    SET data = EXCLUDED.data, last_scraped_at = now()
                """,
                keywords, geo, timeframe, category, gprop, json.dumps(points),
            )

    async def get_compare(self, keywords: list[str], geo: str, timeframe: str, category: int = 0, gprop: str = ""):
        """Returns (points, last_scraped_at) if fresh, else (None, ...). points may be []."""
        ttl_minutes = TIMEFRAME_TTL_MINUTES.get(timeframe, 1440)
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, data, last_scraped_at FROM compare_cache
                WHERE keywords = $1 AND geo = $2 AND timeframe = $3
                  AND category = $4 AND gprop = $5
                """,
                keywords, geo, timeframe, category, gprop,
            )
            if not row:
                return None, None

            cutoff = datetime.datetime.now(tz=datetime.timezone.utc) - datetime.timedelta(minutes=ttl_minutes)
            if row["last_scraped_at"] < cutoff:
                return None, row["last_scraped_at"]

            await conn.execute(
                "UPDATE compare_cache SET last_accessed_at = now() WHERE id = $1",
                row["id"],
            )
            return json.loads(row["data"]), row["last_scraped_at"]

    async def save_widget(self, kind: str, keyword: str, geo: str, timeframe: str, data, category: int = 0, gprop: str = ""):
        """kind: 'regions' | 'related'. data — любой JSON-сериализуемый результат виджета."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO widget_cache (kind, keyword, geo, timeframe, category, gprop, data, last_scraped_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, now())
                ON CONFLICT ON CONSTRAINT widget_cache_unique DO UPDATE
                    SET data = EXCLUDED.data, last_scraped_at = now()
                """,
                kind, keyword, geo, timeframe, category, gprop, json.dumps(data),
            )

    async def get_widget(self, kind: str, keyword: str, geo: str, timeframe: str, category: int = 0, gprop: str = ""):
        """Returns (data, last_scraped_at) if fresh, else (None, ...)."""
        ttl_minutes = TIMEFRAME_TTL_MINUTES.get(timeframe, 1440)
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, data, last_scraped_at FROM widget_cache
                WHERE kind = $1 AND keyword = $2 AND geo = $3 AND timeframe = $4
                  AND category = $5 AND gprop = $6
                """,
                kind, keyword, geo, timeframe, category, gprop,
            )
            if not row:
                return None, None

            cutoff = datetime.datetime.now(tz=datetime.timezone.utc) - datetime.timedelta(minutes=ttl_minutes)
            if row["last_scraped_at"] < cutoff:
                return None, row["last_scraped_at"]

            await conn.execute(
                "UPDATE widget_cache SET last_accessed_at = now() WHERE id = $1",
                row["id"],
            )
            return json.loads(row["data"]), row["last_scraped_at"]

    async def save_trending(self, geo: str, trends: list[dict]):
        """trends: [{"keyword", "volume", "growth_pct", "started_at" (epoch|None), "breakdown", "categories"}]"""
        now = datetime.datetime.now(tz=datetime.timezone.utc)
        rows = []
        for t in trends:
            started = (
                datetime.datetime.fromtimestamp(t["started_at"], tz=datetime.timezone.utc)
                if t["started_at"] else now
            )
            rows.append((geo, t["keyword"], t["volume"], t.get("growth_pct"), started, json.dumps(t["breakdown"]), t["categories"]))

        async with self._pool.acquire() as conn:
            await conn.executemany(
                """
                INSERT INTO trending_searches (geo, keyword, volume, growth_pct, started_at, breakdown, categories)
                VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7)
                ON CONFLICT ON CONSTRAINT trending_searches_unique DO UPDATE
                    SET volume = GREATEST(trending_searches.volume, EXCLUDED.volume),
                        growth_pct = GREATEST(trending_searches.growth_pct, EXCLUDED.growth_pct),
                        breakdown = EXCLUDED.breakdown,
                        last_seen_at = now()
                """,
                rows,
            )

    _TRENDING_SORT = {
        "volume":  "volume DESC NULLS LAST, started_at DESC",
        "growth":  "growth_pct DESC NULLS LAST, volume DESC NULLS LAST",
        "recency": "started_at DESC",
        "title":   "keyword ASC",
    }

    async def get_trending(self, geo: str, since_hours: int = 24, limit: int = 100,
                           category: int | None = None, sort: str = "volume",
                           active_only: bool = False) -> list[dict]:
        cutoff = datetime.datetime.now(tz=datetime.timezone.utc) - datetime.timedelta(hours=since_hours)
        order = self._TRENDING_SORT.get(sort, self._TRENDING_SORT["volume"])

        conditions = ["geo = $1", "last_seen_at >= $2"]
        params: list = [geo, cutoff]
        if category is not None:
            params.append(category)
            conditions.append(f"${len(params)} = ANY(categories)")
        if active_only:
            # активен = присутствовал в самом свежем опросе этого гео
            conditions.append(
                "last_seen_at >= (SELECT max(last_seen_at) - interval '5 minutes' FROM trending_searches WHERE geo = $1)"
            )
        params.append(limit)

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT keyword, volume, growth_pct, started_at, breakdown, categories, first_seen_at, last_seen_at
                FROM trending_searches
                WHERE {' AND '.join(conditions)}
                ORDER BY {order}
                LIMIT ${len(params)}
                """,
                *params,
            )
            return [
                {
                    "keyword": r["keyword"],
                    "volume": r["volume"],
                    "growth_pct": r["growth_pct"],
                    "started_at": r["started_at"].isoformat(),
                    "breakdown": json.loads(r["breakdown"]),
                    "categories": list(r["categories"]),
                    "first_seen_at": r["first_seen_at"].isoformat(),
                }
                for r in rows
            ]

    async def trending_last_poll(self, geo: str):
        """Когда гео опрашивалось последний раз (max last_seen_at)."""
        async with self._pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT max(last_seen_at) FROM trending_searches WHERE geo = $1", geo,
            )
