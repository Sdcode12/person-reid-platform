CREATE TABLE IF NOT EXISTS capture_settings (
    config_key VARCHAR(32) PRIMARY KEY,
    config_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
