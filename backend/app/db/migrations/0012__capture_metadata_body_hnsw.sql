CREATE INDEX IF NOT EXISTS idx_capture_metadata_body_hnsw
ON capture_metadata
USING hnsw (body_vec vector_cosine_ops)
WITH (m = 16, ef_construction = 200);
