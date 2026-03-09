from __future__ import annotations

import time

from app.core.logging import get_logger
from app.core.settings import settings
from app.services.camera_config_store import camera_config_store
from app.services.camera_recognition_service import camera_recognition_service

logger = get_logger("ingestion")


def _enabled_camera_ids() -> list[str]:
    ids: list[str] = []
    for camera in camera_recognition_service.list_camera_configs():
        if not bool(camera.get("enabled", True)):
            continue
        camera_id = str(camera.get("id", "")).strip()
        if camera_id:
            ids.append(camera_id)
    return ids


def main() -> None:
    interval_seconds = max(1, int(settings.ingestion_interval_seconds))
    logger.info("ingestion process started interval_seconds=%s", interval_seconds)
    db_cameras = camera_config_store.load()
    camera_recognition_service.replace_camera_configs(db_cameras)
    camera_recognition_service.start()
    try:
        while True:
            camera_ids = _enabled_camera_ids()
            if not camera_ids:
                logger.warning("no enabled cameras configured")
                time.sleep(interval_seconds)
                continue

            for camera_id in camera_ids:
                result = camera_recognition_service.recognize(camera_id=camera_id, persist=True)
                if result.get("error"):
                    logger.warning(
                        "ingestion skip camera_id=%s error=%s",
                        camera_id,
                        result.get("error"),
                    )
                    continue
                logger.info(
                    "ingestion ok camera_id=%s ts=%s people=%s persisted=%s",
                    camera_id,
                    result.get("timestamp"),
                    result.get("people_count"),
                    result.get("persisted_count"),
                )
            time.sleep(interval_seconds)
    finally:
        camera_recognition_service.stop()


if __name__ == "__main__":
    main()
