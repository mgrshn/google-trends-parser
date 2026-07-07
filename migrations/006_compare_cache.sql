CREATE TABLE IF NOT EXISTS compare_cache (
    id               SERIAL PRIMARY KEY,
    keywords         TEXT[]      NOT NULL,   -- отсортированный список ключей
    geo              TEXT        NOT NULL,
    timeframe        TEXT        NOT NULL,
    category         INTEGER     NOT NULL DEFAULT 0,
    gprop            TEXT        NOT NULL DEFAULT '',
    data             JSONB       NOT NULL,   -- [{"ts": "...", "values": [23, 43]}, ...]
    last_scraped_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_accessed_at TIMESTAMPTZ,
    CONSTRAINT compare_cache_unique UNIQUE (keywords, geo, timeframe, category, gprop)
);

CREATE INDEX IF NOT EXISTS idx_compare_last_accessed ON compare_cache (last_accessed_at NULLS FIRST);