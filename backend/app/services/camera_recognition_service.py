from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Any
import threading
from urllib.parse import quote

import cv2
from psycopg2.extras import Json

from app.core.constants import Color
from app.core.detector import Detection, PersonDetector
from app.core.logging import get_logger
from app.core.settings import settings
from app.db.pool import db_pool
from app.stream.stream_manager import StreamManager

_ZERO_BODY_VECTOR = "[" + ",".join("0" for _ in range(512)) + "]"
_ROI_CACHE_TTL_SECONDS = 5.0
_SNAPSHOT_JPEG_QUALITY = 92


class CameraRecognitionService:
    def __init__(self) -> None:
        self._logger = get_logger("camera.recognition")
        self._lock = threading.RLock()
        self._stream_manager = StreamManager()
        self._detector = PersonDetector(
            mode=settings.detector_mode,
            hit_threshold=settings.detector_hog_hit_threshold,
            yolo_model_path=settings.yolo_model_path,
            yolo_input_size=settings.yolo_input_size,
            confidence_threshold=settings.detector_confidence_threshold,
            nms_threshold=settings.detector_nms_threshold,
            yolo_person_class_ids=settings.yolo_person_class_ids,
        )
        self._persist_tracks_enabled = settings.detector_persist_tracks
        self._snapshot_root = self._resolve_snapshot_root(settings.snapshot_dir)
        self._camera_configs = []
        self._configured_camera_ids = {str(camera.get("id", "")).strip() for camera in self._camera_configs}
        self._roi_cache: dict[str, tuple[float, dict[str, object]]] = {}
        self._started = False

    def start(self) -> None:
        with self._lock:
            if self._started:
                return

            self._stream_manager.configure(
                cameras=self._camera_configs,
                reconnect_interval=settings.stream_reconnect_interval,
                buffer_size=settings.stream_buffer_size,
            )
            self._stream_manager.start_all()
            self._started = True
            self._logger.info("camera recognition service started")

    def stop(self) -> None:
        with self._lock:
            if not self._started:
                return
            self._stream_manager.stop_all()
            self._started = False
            self._logger.info("camera recognition service stopped")

    def list_cameras(self) -> list[dict[str, object]]:
        configured_names = {
            str(c.get("id")): str(c.get("name", c.get("id", ""))) for c in self._camera_configs
        }
        result: list[dict[str, object]] = []
        for item in self._stream_manager.list_status():
            camera_id = str(item["camera_id"])
            result.append(
                {
                    **item,
                    "camera_name": configured_names.get(camera_id, camera_id),
                }
            )
        return result

    def list_camera_configs(self) -> list[dict[str, object]]:
        with self._lock:
            items = [dict(item) for item in self._camera_configs]
        items.sort(key=lambda item: str(item.get("id", "")))
        return items

    def replace_camera_configs(self, cameras: list[dict[str, object]]) -> list[dict[str, object]]:
        normalized = self._normalize_camera_configs(cameras)
        with self._lock:
            self._camera_configs = normalized
            self._configured_camera_ids = {str(camera.get("id", "")).strip() for camera in self._camera_configs}

            stale = [key for key in self._roi_cache.keys() if key not in self._configured_camera_ids]
            for key in stale:
                self._roi_cache.pop(key, None)

            if self._started:
                self._stream_manager.stop_all()
                self._stream_manager.configure(
                    cameras=self._camera_configs,
                    reconnect_interval=settings.stream_reconnect_interval,
                    buffer_size=settings.stream_buffer_size,
                )
                self._stream_manager.start_all()

        return self.list_camera_configs()

    def _normalize_camera_configs(self, raw: list[dict[str, object]] | list[dict[str, Any]]) -> list[dict[str, object]]:
        normalized: list[dict[str, object]] = []
        seen: set[str] = set()
        for camera in raw:
            if not isinstance(camera, dict):
                continue
            camera_id = str(camera.get("id", "")).strip()
            if not camera_id or camera_id in seen:
                continue
            rtsp_url = str(camera.get("rtsp_url", "")).strip()
            host = str(camera.get("host", "")).strip()
            username = str(camera.get("username", "")).strip()
            password = "" if camera.get("password") is None else str(camera.get("password", ""))
            try:
                channel_id = max(1, int(camera.get("channel_id", 1) or 1))
            except Exception:
                channel_id = 1
            if not rtsp_url and host and username and password:
                channel_no = channel_id * 100 + 1
                rtsp_url = (
                    f"rtsp://{quote(username, safe='')}:{quote(password, safe='')}"
                    f"@{host}:554/Streaming/Channels/{channel_no}"
                )
            if not rtsp_url:
                continue
            raw_enabled = camera.get("enabled", True)
            enabled = (
                raw_enabled
                if isinstance(raw_enabled, bool)
                else str(raw_enabled).strip().lower() not in {"0", "false", "no", "off"}
            )
            name = str(camera.get("name", camera_id)).strip() or camera_id
            seen.add(camera_id)
            normalized.append(
                {
                    "id": camera_id,
                    "name": name,
                    "rtsp_url": rtsp_url,
                    "host": host,
                    "username": username,
                    "password": password,
                    "channel_id": channel_id,
                    "enabled": bool(enabled),
                }
            )
        return normalized

    def test_camera(self, camera_id: str) -> dict[str, object]:
        reader = self._stream_manager.get_reader(camera_id)
        if reader is None:
            return {
                "camera_id": camera_id,
                "ok": False,
                "reason": "camera not configured",
            }

        frame, ts = reader.get_latest_frame()
        if frame is None:
            return {
                "camera_id": camera_id,
                "ok": False,
                "reason": "no frame available",
            }

        return {
            "camera_id": camera_id,
            "ok": True,
            "reason": "frame available",
            "last_frame_time": ts.isoformat() if ts else None,
            "shape": [int(frame.shape[0]), int(frame.shape[1]), int(frame.shape[2])],
        }

    def recognize(
        self,
        camera_id: str,
        persist: bool = False,
        apply_roi: bool = True,
    ) -> dict[str, object]:
        reader = self._stream_manager.get_reader(camera_id)
        if reader is None:
            return {
                "camera_id": camera_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "people_count": 0,
                "raw_people_count": 0,
                "dropped_count": 0,
                "detections": [],
                "persisted_count": 0,
                "roi_applied": apply_roi,
                "include_polygon_count": 0,
                "exclude_polygon_count": 0,
                "error": "camera not configured",
            }

        frame, ts = reader.get_latest_frame()
        if frame is None:
            return {
                "camera_id": camera_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "people_count": 0,
                "raw_people_count": 0,
                "dropped_count": 0,
                "detections": [],
                "persisted_count": 0,
                "roi_applied": apply_roi,
                "include_polygon_count": 0,
                "exclude_polygon_count": 0,
                "error": "no frame available",
            }

        capture_time = ts if ts else datetime.now(timezone.utc)
        raw_detections = self._detector.detect(frame)
        detections, roi_stats = self._apply_roi_filter(camera_id, frame, raw_detections, apply_roi=apply_roi)
        serialized = [
            {
                "bbox": list(det.bbox),
                "confidence": round(det.confidence, 4),
            }
            for det in detections
        ]
        persisted_track_ids: list[int] = []
        should_persist = persist and self._persist_tracks_enabled and bool(detections)
        if should_persist:
            persisted_track_ids = self._persist_detections(
                camera_id=camera_id,
                capture_time=capture_time,
                frame=frame,
                detections=detections,
            )

        return {
            "camera_id": camera_id,
            "timestamp": capture_time.isoformat(),
            "people_count": len(serialized),
            "raw_people_count": len(raw_detections),
            "dropped_count": max(0, len(raw_detections) - len(serialized)),
            "detections": serialized,
            "persisted_count": len(persisted_track_ids),
            "roi_applied": apply_roi,
            "include_polygon_count": int(roi_stats["include_polygon_count"]),
            "exclude_polygon_count": int(roi_stats["exclude_polygon_count"]),
            "error": None,
        }

    def snapshot(self, camera_id: str, draw_boxes: bool, apply_roi: bool = True) -> bytes | None:
        reader = self._stream_manager.get_reader(camera_id)
        if reader is None:
            return None

        frame, _ = reader.get_latest_frame()
        if frame is None:
            return None

        output = frame
        if draw_boxes:
            output = frame.copy()
            raw_detections = self._detector.detect(frame)
            detections, _ = self._apply_roi_filter(
                camera_id,
                frame,
                raw_detections,
                apply_roi=apply_roi,
            )
            for det in detections:
                x, y, w, h = det.bbox
                cv2.rectangle(output, (x, y), (x + w, y + h), (0, 200, 0), 2)
                cv2.putText(
                    output,
                    f"person {det.confidence:.2f}",
                    (x, max(10, y - 8)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.5,
                    (0, 200, 0),
                    1,
                    cv2.LINE_AA,
                )

        ok, encoded = cv2.imencode(".jpg", output)
        if not ok:
            return None
        return encoded.tobytes()

    def get_roi_config(self, camera_id: str) -> dict[str, object] | None:
        if not self._is_configured_camera(camera_id):
            return None

        cache_key = camera_id.strip()
        cached = self._roi_cache.get(cache_key)
        now = monotonic()
        if cached and now - cached[0] <= _ROI_CACHE_TTL_SECONDS:
            return self._copy_roi_config(cached[1])

        config = self._load_roi_config_from_db(cache_key)
        self._roi_cache[cache_key] = (now, config)
        return self._copy_roi_config(config)

    def update_roi_config(
        self,
        camera_id: str,
        include: list[list[list[float]]],
        exclude: list[list[list[float]]],
        updated_by: str,
    ) -> dict[str, object] | None:
        if not self._is_configured_camera(camera_id):
            return None

        normalized_include = self._normalize_polygons(include)
        normalized_exclude = self._normalize_polygons(exclude)
        cache_key = camera_id.strip()
        updated_at = datetime.now(timezone.utc)
        config = {
            "camera_id": cache_key,
            "include": normalized_include,
            "exclude": normalized_exclude,
            "updated_by": updated_by,
            "updated_at": updated_at,
        }

        pool = None
        conn = None
        try:
            pool = db_pool.get_pool()
            conn = pool.getconn()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO camera_roi_configs (
                        camera_id,
                        include_polygons,
                        exclude_polygons,
                        updated_by,
                        updated_at
                    )
                    VALUES (%s, %s, %s, %s, NOW())
                    ON CONFLICT (camera_id) DO UPDATE SET
                        include_polygons = EXCLUDED.include_polygons,
                        exclude_polygons = EXCLUDED.exclude_polygons,
                        updated_by = EXCLUDED.updated_by,
                        updated_at = NOW()
                    RETURNING updated_at
                    """,
                    (
                        cache_key,
                        Json(normalized_include),
                        Json(normalized_exclude),
                        updated_by,
                    ),
                )
                row = cur.fetchone()
                if row and row[0]:
                    config["updated_at"] = row[0]
            conn.commit()
        except Exception as exc:
            if conn is not None:
                conn.rollback()
            self._logger.warning("failed to upsert camera roi camera_id=%s error=%s", cache_key, exc)
            return None
        finally:
            if pool is not None and conn is not None:
                pool.putconn(conn)

        self._roi_cache[cache_key] = (monotonic(), config)
        return self._copy_roi_config(config)

    def test_roi_filter(self, camera_id: str) -> dict[str, object]:
        reader = self._stream_manager.get_reader(camera_id)
        if reader is None:
            return {
                "camera_id": camera_id,
                "error": "camera not configured",
            }

        frame, ts = reader.get_latest_frame()
        if frame is None:
            return {
                "camera_id": camera_id,
                "error": "no frame available",
            }

        capture_time = ts if ts else datetime.now(timezone.utc)
        raw_detections = self._detector.detect(frame)
        filtered_detections, roi_stats = self._apply_roi_filter(
            camera_id,
            frame,
            raw_detections,
            apply_roi=True,
        )

        return {
            "camera_id": camera_id,
            "timestamp": capture_time,
            "raw_people_count": len(raw_detections),
            "filtered_people_count": len(filtered_detections),
            "dropped_count": max(0, len(raw_detections) - len(filtered_detections)),
            "include_polygon_count": int(roi_stats["include_polygon_count"]),
            "exclude_polygon_count": int(roi_stats["exclude_polygon_count"]),
        }

    def _is_configured_camera(self, camera_id: str) -> bool:
        return camera_id.strip() in self._configured_camera_ids

    def _apply_roi_filter(
        self,
        camera_id: str,
        frame: Any,
        detections: list[Detection],
        apply_roi: bool,
    ) -> tuple[list[Detection], dict[str, int]]:
        if not detections:
            return detections, {
                "include_polygon_count": 0,
                "exclude_polygon_count": 0,
            }
        if not apply_roi:
            return detections, {
                "include_polygon_count": 0,
                "exclude_polygon_count": 0,
            }

        config = self.get_roi_config(camera_id)
        if not config:
            return detections, {
                "include_polygon_count": 0,
                "exclude_polygon_count": 0,
            }

        include_polygons = self._parse_polygons(config.get("include", []))
        exclude_polygons = self._parse_polygons(config.get("exclude", []))
        if not include_polygons and not exclude_polygons:
            return detections, {
                "include_polygon_count": 0,
                "exclude_polygon_count": 0,
            }

        frame_h, frame_w = frame.shape[:2]
        filtered: list[Detection] = []
        for det in detections:
            center_x = (det.bbox[0] + det.bbox[2] * 0.5) / max(1, frame_w)
            center_y = (det.bbox[1] + det.bbox[3] * 0.5) / max(1, frame_h)

            inside_include = True
            if include_polygons:
                inside_include = self._point_in_any_polygon(center_x, center_y, include_polygons)

            inside_exclude = False
            if exclude_polygons:
                inside_exclude = self._point_in_any_polygon(center_x, center_y, exclude_polygons)

            if inside_include and not inside_exclude:
                filtered.append(det)

        return filtered, {
            "include_polygon_count": len(include_polygons),
            "exclude_polygon_count": len(exclude_polygons),
        }

    def _parse_polygons(self, raw: object) -> list[list[tuple[float, float]]]:
        parsed: list[list[tuple[float, float]]] = []
        if not isinstance(raw, list):
            return parsed

        for polygon in raw:
            if not isinstance(polygon, list):
                continue
            points: list[tuple[float, float]] = []
            for point in polygon:
                if isinstance(point, dict):
                    x = point.get("x")
                    y = point.get("y")
                elif isinstance(point, (list, tuple)) and len(point) >= 2:
                    x = point[0]
                    y = point[1]
                else:
                    continue

                x_num = self._to_norm(x)
                y_num = self._to_norm(y)
                if x_num is None or y_num is None:
                    continue
                points.append((x_num, y_num))

            if len(points) >= 3:
                parsed.append(points)
        return parsed

    def _normalize_polygons(self, raw: object) -> list[list[list[float]]]:
        parsed = self._parse_polygons(raw)
        return [[[round(x, 6), round(y, 6)] for x, y in polygon] for polygon in parsed]

    def _to_norm(self, value: object) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return max(0.0, min(1.0, number))

    def _point_in_any_polygon(
        self,
        x: float,
        y: float,
        polygons: list[list[tuple[float, float]]],
    ) -> bool:
        return any(self._point_in_polygon(x, y, polygon) for polygon in polygons)

    def _point_in_polygon(
        self,
        x: float,
        y: float,
        polygon: list[tuple[float, float]],
    ) -> bool:
        inside = False
        j = len(polygon) - 1
        for i in range(len(polygon)):
            xi, yi = polygon[i]
            xj, yj = polygon[j]
            crossed = (yi > y) != (yj > y)
            if crossed:
                denominator = (yj - yi) if (yj - yi) != 0 else 1e-9
                x_on_edge = (xj - xi) * (y - yi) / denominator + xi
                if x <= x_on_edge:
                    inside = not inside
            j = i
        return inside

    def _default_roi_config(self, camera_id: str) -> dict[str, object]:
        return {
            "camera_id": camera_id,
            "include": [],
            "exclude": [],
            "updated_by": "system",
            "updated_at": None,
        }

    def _copy_roi_config(self, config: dict[str, object]) -> dict[str, object]:
        include = self._normalize_polygons(config.get("include", []))
        exclude = self._normalize_polygons(config.get("exclude", []))
        return {
            "camera_id": str(config.get("camera_id", "")),
            "include": include,
            "exclude": exclude,
            "updated_by": str(config.get("updated_by", "system")),
            "updated_at": config.get("updated_at"),
        }

    def _load_roi_config_from_db(self, camera_id: str) -> dict[str, object]:
        default_config = self._default_roi_config(camera_id)
        pool = None
        conn = None
        try:
            pool = db_pool.get_pool()
            conn = pool.getconn()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT include_polygons, exclude_polygons, updated_by, updated_at
                    FROM camera_roi_configs
                    WHERE camera_id = %s
                    """,
                    (camera_id,),
                )
                row = cur.fetchone()
                if not row:
                    return default_config

                return {
                    "camera_id": camera_id,
                    "include": self._normalize_polygons(row[0]),
                    "exclude": self._normalize_polygons(row[1]),
                    "updated_by": str(row[2] or "system"),
                    "updated_at": row[3],
                }
        except Exception as exc:
            self._logger.warning("failed to load camera roi camera_id=%s error=%s", camera_id, exc)
            return default_config
        finally:
            if pool is not None and conn is not None:
                pool.putconn(conn)

    def _persist_detections(
        self,
        camera_id: str,
        capture_time: datetime,
        frame: Any,
        detections: list[Detection],
    ) -> list[int]:
        if capture_time.tzinfo is None:
            capture_time = capture_time.replace(tzinfo=timezone.utc)

        inserted_track_ids: list[int] = []
        pool = None
        conn = None
        try:
            pool = db_pool.get_pool()
            conn = pool.getconn()
            with conn.cursor() as cur:
                for det in detections:
                    cur.execute(
                        """
                        INSERT INTO tracks (
                            camera_id,
                            start_time,
                            end_time,
                            duration,
                            body_vec,
                            has_face,
                            upper_color,
                            lower_color,
                            gender,
                            has_hat,
                            has_backpack,
                            is_cycling,
                            sleeve_length,
                            quality_score,
                            frame_count
                        )
                        VALUES (
                            %s, %s, %s, %s, %s::vector,
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                        )
                        RETURNING track_id
                        """,
                        (
                            camera_id,
                            capture_time,
                            capture_time,
                            0.0,
                            _ZERO_BODY_VECTOR,
                            False,
                            Color.UNKNOWN,
                            Color.UNKNOWN,
                            "unknown",
                            None,
                            None,
                            False,
                            "unknown",
                            float(det.confidence),
                            1,
                        ),
                    )
                    row = cur.fetchone()
                    if row:
                        track_id = int(row[0])
                        inserted_track_ids.append(track_id)
                        image_path = self._save_snapshot_image(
                            frame=frame,
                            detection=det,
                            camera_id=camera_id,
                            track_id=track_id,
                            capture_time=capture_time,
                        )
                        if image_path:
                            cur.execute(
                                """
                                INSERT INTO snapshots (
                                    track_id,
                                    image_path,
                                    timestamp,
                                    is_best
                                )
                                VALUES (%s, %s, %s, %s)
                                """,
                                (track_id, image_path, capture_time, True),
                            )
            conn.commit()
        except Exception as exc:
            if conn is not None:
                conn.rollback()
            self._logger.warning(
                "failed to persist recognition result camera_id=%s error=%s",
                camera_id,
                exc,
            )
            return []
        finally:
            if pool is not None and conn is not None:
                pool.putconn(conn)

        return inserted_track_ids

    def _resolve_snapshot_root(self, snapshot_dir: str) -> Path:
        configured = Path(snapshot_dir.strip() or "snapshots")
        if configured.is_absolute():
            return configured

        backend_root = Path(__file__).resolve().parents[2]
        return backend_root / configured

    def _safe_camera_dir(self, camera_id: str) -> str:
        clean = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in camera_id.strip())
        return clean or "unknown_camera"

    def _save_snapshot_image(
        self,
        frame: Any,
        detection: Detection,
        camera_id: str,
        track_id: int,
        capture_time: datetime,
    ) -> str | None:
        if frame is None:
            return None

        x, y, w, h = detection.bbox
        frame_h, frame_w = frame.shape[:2]
        x1 = max(0, min(x, frame_w - 1))
        y1 = max(0, min(y, frame_h - 1))
        x2 = max(x1 + 1, min(x + w, frame_w))
        y2 = max(y1 + 1, min(y + h, frame_h))
        crop = frame[y1:y2, x1:x2]
        if crop.size == 0:
            crop = frame

        dt = capture_time.astimezone(timezone.utc)
        day = dt.strftime("%Y-%m-%d")
        camera_dir = self._safe_camera_dir(camera_id)
        relative = Path(day) / camera_dir / f"track_{track_id}_best.jpg"
        disk_path = self._snapshot_root / relative
        disk_path.parent.mkdir(parents=True, exist_ok=True)

        ok, encoded = cv2.imencode(".jpg", crop, [int(cv2.IMWRITE_JPEG_QUALITY), _SNAPSHOT_JPEG_QUALITY])
        if not ok:
            self._logger.warning(
                "failed to encode snapshot camera_id=%s track_id=%s",
                camera_id,
                track_id,
            )
            return None

        try:
            disk_path.write_bytes(encoded.tobytes())
        except Exception as exc:
            self._logger.warning(
                "failed to write snapshot camera_id=%s track_id=%s error=%s",
                camera_id,
                track_id,
                exc,
            )
            return None

        db_root_name = self._snapshot_root.name or "snapshots"
        return f"/{db_root_name}/{relative.as_posix()}"


camera_recognition_service = CameraRecognitionService()
