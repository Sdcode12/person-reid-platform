CREATE TABLE IF NOT EXISTS camera_source_configs (
    camera_id VARCHAR(64) PRIMARY KEY,
    camera_name VARCHAR(128) NOT NULL,
    rtsp_url TEXT NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    sort_order INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_camera_source_configs_enabled_order
ON camera_source_configs(enabled, sort_order, camera_id);
