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

CREATE INDEX IF NOT EXISTS idx_search_queries_created_at
    ON search_queries(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_search_queries_created_by_created_at
    ON search_queries(created_by, created_at DESC);
