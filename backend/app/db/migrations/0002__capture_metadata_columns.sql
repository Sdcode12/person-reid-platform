ALTER TABLE capture_metadata
ADD COLUMN IF NOT EXISTS target_key TEXT;

ALTER TABLE capture_metadata
ADD COLUMN IF NOT EXISTS image_bytes BYTEA;

ALTER TABLE capture_metadata
ADD COLUMN IF NOT EXISTS image_mime_type TEXT;

ALTER TABLE capture_metadata
ADD COLUMN IF NOT EXISTS image_size INTEGER;

CREATE INDEX IF NOT EXISTS idx_capture_metadata_target_key_time
ON capture_metadata(target_key, captured_at DESC);

