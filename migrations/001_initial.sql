CREATE TABLE IF NOT EXISTS keywords (
    id         SERIAL PRIMARY KEY,
    keyword    TEXT        NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS trend_series (
    id              SERIAL PRIMARY KEY,
    keyword_id      INTEGER     NOT NULL REFERENCES keywords(id),
    geo             TEXT        NOT NULL,
    timeframe       TEXT        NOT NULL,
    resolution      TEXT        NOT NULL,
    last_scraped_at TIMESTAMPTZ,
    UNIQUE (keyword_id, geo, timeframe)
);

CREATE TABLE IF NOT EXISTS trend_points (
    series_id INTEGER     NOT NULL,
    point_ts  TIMESTAMPTZ NOT NULL,
    value     SMALLINT    NOT NULL,
    PRIMARY KEY (series_id, point_ts)
) PARTITION BY RANGE (point_ts);

CREATE INDEX IF NOT EXISTS idx_series_keyword ON trend_series (keyword_id);
