from __future__ import annotations

from app.core.logging import get_logger
from app.stream.rtsp_reader import RTSPReader


class StreamManager:
    def __init__(self) -> None:
        self._logger = get_logger("stream.manager")
        self._readers: dict[str, RTSPReader] = {}

    def configure(
        self,
        cameras: list[dict[str, object]],
        reconnect_interval: int,
        buffer_size: int,
    ) -> None:
        self._readers.clear()
        for cam in cameras:
            camera_id = str(cam.get("id", "")).strip()
            rtsp_url = str(cam.get("rtsp_url", "")).strip()
            raw_enabled = cam.get("enabled", True)
            enabled = (
                raw_enabled
                if isinstance(raw_enabled, bool)
                else str(raw_enabled).strip().lower() not in {"0", "false", "no", "off"}
            )
            if not camera_id or not rtsp_url or not enabled:
                continue

            self._readers[camera_id] = RTSPReader(
                camera_id=camera_id,
                rtsp_url=rtsp_url,
                reconnect_interval=reconnect_interval,
                buffer_size=buffer_size,
            )

        self._logger.info("configured camera readers count=%s", len(self._readers))

    def start_all(self) -> None:
        for reader in self._readers.values():
            reader.start()

    def stop_all(self) -> None:
        for reader in self._readers.values():
            reader.stop()

    def get_reader(self, camera_id: str) -> RTSPReader | None:
        return self._readers.get(camera_id)

    def list_status(self) -> list[dict[str, object]]:
        return [reader.get_status() for reader in self._readers.values()]

    def list_camera_ids(self) -> list[str]:
        return sorted(self._readers.keys())
