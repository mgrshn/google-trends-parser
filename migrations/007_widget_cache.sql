-- Кеш виджетов Google Trends: interest by region, related queries.
-- Результат читается/пишется целиком — JSONB, по образцу compare_cache.
CREATE TABLE IF NOT EXISTS widget_cache (
    id               SERIAL PRIMARY KEY,
    kind             TEXT        NOT NULL,   -- 'regions' | 'related'
    keyword          TEXT        NOT NULL,
    geo              TEXT        NOT NULL,
    timeframe        TEXT        NOT NULL,
    category         INTEGER     NOT NULL DEFAULT 0,
    gprop            TEXT        NOT NULL DEFAULT '',
    data             JSONB       NOT NULL,
    last_scraped_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_accessed_at TIMESTAMPTZ,
    CONSTRAINT widget_cache_unique UNIQUE (kind, keyword, geo, timeframe, category, gprop)
);

CREATE INDEX IF NOT EXISTS idx_widget_last_accessed ON widget_cache (last_accessed_at NULLS FIRST);