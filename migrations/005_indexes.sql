CREATE INDEX IF NOT EXISTS idx_series_last_accessed ON trend_series (last_accessed_at NULLS FIRST);
CREATE INDEX IF NOT EXISTS idx_series_last_scraped  ON trend_series (last_scraped_at);
