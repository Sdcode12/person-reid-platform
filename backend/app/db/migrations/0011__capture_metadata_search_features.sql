ALTER TABLE capture_metadata
    ADD COLUMN IF NOT EXISTS upper_color_conf REAL;

ALTER TABLE capture_metadata
    ADD COLUMN IF NOT EXISTS lower_color_conf REAL;

ALTER TABLE capture_metadata
    ADD COLUMN IF NOT EXISTS upper_embedding JSONB NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE capture_metadata
    ADD COLUMN IF NOT EXISTS lower_embedding JSONB NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE capture_metadata
    ADD COLUMN IF NOT EXISTS face_embedding JSONB NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE capture_metadata
    ADD COLUMN IF NOT EXISTS face_confidence REAL;
