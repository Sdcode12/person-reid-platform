from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

import cv2
import numpy as np

from app.core.logging import get_logger


class RTSPReader:
    def __init__(
        self,
        camera_id: str,
        rtsp_url: str,
        reconnect_interval: int = 10,
        buffer_size: int = 1,
    ) -> None:
        self.camera_id = camera_id
        self.rtsp_url = rtsp_url
        self.reconnect_interval = max(1, reconnect_interval)
        self.buffer_size = max(1, buffer_size)

        self._logger = get_logger(f"stream.{camera_id}")
        self._running = False
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

        self._latest_frame: np.ndarray | None = None
        self._last_frame_time: datetime | None = None

        self._frames_read = 0
        self._failures = 0
        self._online = False

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()
        self._logger.info("reader started")

    def stop(self) -> None:
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        self._logger.info("reader stopped")

    def get_latest_frame(self) -> tuple[np.ndarray | None, datetime | None]:
        with self._lock:
            if self._latest_frame is None:
                return None, self._last_frame_time
            return self._latest_frame.copy(), self._last_frame_time

    def get_status(self) -> dict[str, object]:
        with self._lock:
            last_frame = self._last_frame_time.isoformat() if self._last_frame_time else None
            return {
                "camera_id": self.camera_id,
                "online": self._online,
                "last_frame_time": last_frame,
                "frames_read": self._frames_read,
                "failures": self._failures,
            }

    def _read_loop(self) -> None:
        cap: cv2.VideoCapture | None = None

        while self._running:
            if cap is None or not cap.isOpened():
                cap = self._open_capture()
                if cap is None:
                    self._mark_offline()
                    time.sleep(self.reconnect_interval)
                    continue
                self._mark_online()

            ok, frame = cap.read()
            if not ok or frame is None:
                self._logger.warning("read frame failed, reconnecting")
                self._mark_failure()
                if cap is not None:
                    cap.release()
                cap = None
                time.sleep(self.reconnect_interval)
                continue

            with self._lock:
                self._latest_frame = frame
                self._last_frame_time = datetime.now(timezone.utc)
                self._frames_read += 1

        if cap is not None:
            cap.release()

    def _open_capture(self) -> cv2.VideoCapture | None:
        cap = cv2.VideoCapture(self.rtsp_url)
        if not cap.isOpened():
            self._logger.warning("camera open failed")
            return None
        cap.set(cv2.CAP_PROP_BUFFERSIZE, float(self.buffer_size))
        return cap

    def _mark_online(self) -> None:
        with self._lock:
            self._online = True

    def _mark_offline(self) -> None:
        with self._lock:
            self._online = False

    def _mark_failure(self) -> None:
        with self._lock:
            self._online = False
            self._failures += 1
