ALTER TABLE capture_metadata
ADD COLUMN IF NOT EXISTS image_mode VARCHAR(24) NOT NULL DEFAULT 'unknown';

UPDATE capture_metadata
SET image_mode = CASE
    WHEN COALESCE(TRIM(raw->>'image_mode'), '') <> '' THEN LOWER(TRIM(raw->>'image_mode'))
    WHEN is_night IS TRUE THEN 'low_light_color'
    WHEN is_night IS FALSE THEN 'color'
    ELSE 'unknown'
END
WHERE image_mode = 'unknown';

CREATE INDEX IF NOT EXISTS idx_capture_metadata_image_mode_time
ON capture_metadata(image_mode, captured_at DESC);
