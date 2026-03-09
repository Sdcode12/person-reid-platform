CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS users (
    user_id BIGSERIAL PRIMARY KEY,
    username VARCHAR(64) UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role VARCHAR(16) NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS tracks (
    track_id BIGSERIAL PRIMARY KEY,
    camera_id VARCHAR(50) NOT NULL,
    start_time TIMESTAMPTZ NOT NULL,
    end_time TIMESTAMPTZ NOT NULL,
    duration REAL NOT NULL,
    body_vec VECTOR(512) NOT NULL,
    face_vec VECTOR(512),
    has_face BOOLEAN DEFAULT FALSE,
    upper_color VARCHAR(20) NOT NULL DEFAULT 'unknown',
    lower_color VARCHAR(20) NOT NULL DEFAULT 'unknown',
    gender VARCHAR(10) DEFAULT 'unknown',
    has_hat BOOLEAN,
    has_backpack BOOLEAN,
    is_cycling BOOLEAN DEFAULT FALSE,
    sleeve_length VARCHAR(10) DEFAULT 'unknown',
    quality_score REAL DEFAULT 0.0,
    frame_count INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS snapshots (
    snapshot_id BIGSERIAL PRIMARY KEY,
    track_id BIGINT NOT NULL REFERENCES tracks(track_id) ON DELETE CASCADE,
    image_path TEXT NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    is_best BOOLEAN DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS dedup_cache (
    cache_id BIGSERIAL PRIMARY KEY,
    body_vec VECTOR(512) NOT NULL,
    camera_id VARCHAR(50) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS search_feedback (
    feedback_id BIGSERIAL PRIMARY KEY,
    query_id VARCHAR(64) NOT NULL,
    track_id BIGINT NOT NULL,
    verdict VARCHAR(8) NOT NULL,
    note TEXT,
    created_by VARCHAR(64) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS search_queries (
    query_id VARCHAR(64) PRIMARY KEY,
    created_by VARCHAR(64) NOT NULL,
    upper_color VARCHAR(20),
    lower_color VARCHAR(20),
    time_start TIMESTAMPTZ,
    time_end TIMESTAMPTZ,
    camera_id VARCHAR(64),
    image_mode VARCHAR(24),
    has_hat BOOLEAN,
    pose_hint VARCHAR(32),
    min_quality_score REAL,
    face_mode VARCHAR(16) NOT NULL DEFAULT 'assist',
    group_by_target BOOLEAN NOT NULL DEFAULT TRUE,
    diverse_camera BOOLEAN NOT NULL DEFAULT TRUE,
    top_k INTEGER NOT NULL DEFAULT 10,
    result_count INTEGER NOT NULL DEFAULT 0,
    elapsed_ms INTEGER NOT NULL DEFAULT 0,
    funnel JSONB NOT NULL DEFAULT '{}'::jsonb,
    metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS alerts (
    alert_id BIGSERIAL PRIMARY KEY,
    level VARCHAR(16) NOT NULL,
    source VARCHAR(64) NOT NULL,
    message TEXT NOT NULL,
    acknowledged BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS camera_roi_configs (
    camera_id VARCHAR(50) PRIMARY KEY,
    include_polygons JSONB NOT NULL DEFAULT '[]'::jsonb,
    exclude_polygons JSONB NOT NULL DEFAULT '[]'::jsonb,
    updated_by VARCHAR(64) NOT NULL DEFAULT 'system',
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS capture_metadata (
    meta_id BIGSERIAL PRIMARY KEY,
    image_path TEXT NOT NULL UNIQUE,
    camera_id VARCHAR(64) NOT NULL,
    captured_at TIMESTAMPTZ NOT NULL,
    upper_color VARCHAR(20) NOT NULL DEFAULT 'unknown',
    upper_color_conf REAL,
    lower_color VARCHAR(20) NOT NULL DEFAULT 'unknown',
    lower_color_conf REAL,
    head_color VARCHAR(20) NOT NULL DEFAULT 'unknown',
    has_hat BOOLEAN,
    image_mode VARCHAR(24) NOT NULL DEFAULT 'unknown',
    is_night BOOLEAN,
    pose_hint VARCHAR(32),
    target_key TEXT,
    quality_score REAL,
    people_count INTEGER,
    person_confidence REAL,
    person_area_ratio REAL,
    body_vec VECTOR(512) NOT NULL,
    upper_embedding JSONB NOT NULL DEFAULT '[]'::jsonb,
    lower_embedding JSONB NOT NULL DEFAULT '[]'::jsonb,
    face_embedding JSONB NOT NULL DEFAULT '[]'::jsonb,
    face_confidence REAL,
    image_bytes BYTEA,
    image_mime_type TEXT,
    image_size INTEGER,
    raw JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tracks_time ON tracks(start_time, end_time);
CREATE INDEX IF NOT EXISTS idx_tracks_color_time ON tracks(upper_color, lower_color, start_time);
CREATE INDEX IF NOT EXISTS idx_snapshots_track ON snapshots(track_id);
CREATE INDEX IF NOT EXISTS idx_dedup_expires ON dedup_cache(expires_at);
CREATE INDEX IF NOT EXISTS idx_feedback_query ON search_feedback(query_id);
CREATE INDEX IF NOT EXISTS idx_search_queries_created_at ON search_queries(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_search_queries_created_by_created_at ON search_queries(created_by, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_alert_created_at ON alerts(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_camera_roi_updated_at ON camera_roi_configs(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_capture_metadata_time ON capture_metadata(captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_capture_metadata_camera_time ON capture_metadata(camera_id, captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_capture_metadata_color_time ON capture_metadata(upper_color, lower_color, captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_capture_metadata_quality ON capture_metadata(quality_score DESC);
CREATE INDEX IF NOT EXISTS idx_capture_metadata_target_key_time ON capture_metadata(target_key, captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_capture_metadata_image_mode_time ON capture_metadata(image_mode, captured_at DESC);
