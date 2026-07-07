-- История трендовых запросов (Trending Now) по гео.
-- Один тренд живёт в выдаче Google часы/дни; при повторных опросах
-- обновляем volume и last_seen_at по ключу (geo, keyword, started_at).
CREATE TABLE IF NOT EXISTS trending_searches (
    id            BIGSERIAL PRIMARY KEY,
    geo           TEXT        NOT NULL,
    keyword       TEXT        NOT NULL,
    volume        BIGINT,                  -- примерный объём поиска от Google
    started_at    TIMESTAMPTZ NOT NULL,    -- когда тренд начался (по данным Google)
    breakdown     JSONB       NOT NULL DEFAULT '[]',  -- связанные вариации запроса
    categories    INTEGER[]   NOT NULL DEFAULT '{}',
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT trending_searches_unique UNIQUE (geo, keyword, started_at)
);

CREATE INDEX IF NOT EXISTS idx_trending_geo_seen ON trending_searches (geo, last_seen_at DESC);
CREATE INDEX IF NOT EXISTS idx_trending_first_seen ON trending_searches (first_seen_at);