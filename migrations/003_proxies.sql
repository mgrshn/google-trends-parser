CREATE TABLE IF NOT EXISTS proxies (
    id            SERIAL PRIMARY KEY,
    url           TEXT        NOT NULL UNIQUE,
    enabled       BOOLEAN     NOT NULL DEFAULT true,
    added_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at  TIMESTAMPTZ,
    success_count INTEGER     NOT NULL DEFAULT 0,
    fail_count    INTEGER     NOT NULL DEFAULT 0
);