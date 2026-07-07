ALTER TABLE trend_series ADD COLUMN IF NOT EXISTS category INTEGER NOT NULL DEFAULT 0;
ALTER TABLE trend_series ADD COLUMN IF NOT EXISTS gprop    TEXT    NOT NULL DEFAULT '';

ALTER TABLE trend_series DROP CONSTRAINT IF EXISTS trend_series_keyword_id_geo_timeframe_key;
ALTER TABLE trend_series ADD CONSTRAINT trend_series_unique
    UNIQUE (keyword_id, geo, timeframe, category, gprop);

ALTER TABLE trend_series ADD COLUMN IF NOT EXISTS last_accessed_at TIMESTAMPTZ;