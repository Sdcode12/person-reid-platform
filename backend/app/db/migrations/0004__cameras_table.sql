CREATE TABLE IF NOT EXISTS cameras (
    id VARCHAR(64) PRIMARY KEY,
    name VARCHAR(128) NOT NULL,
    rtsp_url TEXT,
    host VARCHAR(255),
    port INTEGER NOT NULL DEFAULT 80,
    scheme VARCHAR(16) NOT NULL DEFAULT 'http',
    username VARCHAR(128),
    password VARCHAR(256),
    channel_id INTEGER NOT NULL DEFAULT 1,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    sort_order INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE cameras ADD COLUMN IF NOT EXISTS name VARCHAR(128);
ALTER TABLE cameras ADD COLUMN IF NOT EXISTS rtsp_url TEXT;
ALTER TABLE cameras ADD COLUMN IF NOT EXISTS host VARCHAR(255);
ALTER TABLE cameras ADD COLUMN IF NOT EXISTS port INTEGER NOT NULL DEFAULT 80;
ALTER TABLE cameras ADD COLUMN IF NOT EXISTS scheme VARCHAR(16) NOT NULL DEFAULT 'http';
ALTER TABLE cameras ADD COLUMN IF NOT EXISTS username VARCHAR(128);
ALTER TABLE cameras ADD COLUMN IF NOT EXISTS password VARCHAR(256);
ALTER TABLE cameras ADD COLUMN IF NOT EXISTS channel_id INTEGER NOT NULL DEFAULT 1;
ALTER TABLE cameras ADD COLUMN IF NOT EXISTS enabled BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE cameras ADD COLUMN IF NOT EXISTS sort_order INTEGER NOT NULL DEFAULT 0;
ALTER TABLE cameras ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE cameras ADD COLUMN IF NOT EXISTS id VARCHAR(64);

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = 'cameras' AND column_name = 'camera_id'
    ) THEN
        IF EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_name = 'cameras' AND column_name = 'id'
        ) THEN
            EXECUTE 'UPDATE cameras SET id = COALESCE(id, camera_id::text)';
        END IF;
    END IF;

    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = 'cameras' AND column_name = 'camera_name'
    ) THEN
        IF EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_name = 'cameras' AND column_name = 'name'
        ) THEN
            EXECUTE 'UPDATE cameras SET name = COALESCE(name, camera_name::text)';
        END IF;
    END IF;
END
$$;

DO $$
BEGIN
    IF to_regclass('camera_source_configs') IS NOT NULL THEN
        IF EXISTS (SELECT 1 FROM camera_source_configs)
           AND NOT EXISTS (SELECT 1 FROM cameras)
        THEN
            INSERT INTO cameras (
                id,
                name,
                rtsp_url,
                enabled,
                sort_order,
                updated_at
            )
            SELECT
                camera_id,
                camera_name,
                rtsp_url,
                enabled,
                sort_order,
                COALESCE(updated_at, NOW())
            FROM camera_source_configs;
        END IF;
    END IF;
END
$$;

CREATE INDEX IF NOT EXISTS idx_cameras_enabled_order
ON cameras(enabled, sort_order, id);
