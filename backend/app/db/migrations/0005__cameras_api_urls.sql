ALTER TABLE cameras ADD COLUMN IF NOT EXISTS event_api_url TEXT;
ALTER TABLE cameras ADD COLUMN IF NOT EXISTS snapshot_api_url TEXT;

UPDATE cameras
SET event_api_url = COALESCE(event_api_url, '')
WHERE event_api_url IS NULL;

UPDATE cameras
SET snapshot_api_url = COALESCE(snapshot_api_url, '')
WHERE snapshot_api_url IS NULL;
