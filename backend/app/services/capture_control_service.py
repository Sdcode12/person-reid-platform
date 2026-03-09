from __future__ import annotations

import copy
import json
import mimetypes
import os
import re
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse
from urllib.parse import urlunparse

import httpx
import yaml

from app.db.pool import db_pool
from app.core.logging import get_logger
from app.services.camera_config_store import camera_config_store


class CaptureControlService:
    def __init__(self) -> None:
        self._logger = get_logger("capture.control")
        self._lock = threading.RLock()
        self._logs: deque[dict[str, str]] = deque(maxlen=2000)
        self._processes: dict[str, subprocess.Popen[str]] = {}
        self._reader_threads: dict[str, threading.Thread] = {}
        self._started_at_by_camera: dict[str, datetime] = {}
        self._last_exit_code_by_camera: dict[str, int | None] = {}
        self._command_by_camera: dict[str, list[str]] = {}
        self._runtime_config_by_camera: dict[str, Path] = {}
        self._last_exit_code: int | None = None
        self._desired_running = False
        self._desired_camera_ids: set[str] = set()
        self._restart_pending_by_camera: set[str] = set()
        self._restart_count = 0
        self._restart_count_by_camera: dict[str, int] = {}
        self._restart_history_by_camera: dict[str, deque[float]] = {}
        self._auto_restart_enabled = os.getenv("REID_CAPTURE_AUTORESTART", "true").strip().lower() not in {
            "0",
            "false",
            "no",
        }
        self._restart_delay_seconds = max(1.0, float(os.getenv("REID_CAPTURE_RESTART_DELAY_SECONDS", "3")))
        self._restart_max_retries = max(1, int(os.getenv("REID_CAPTURE_RESTART_MAX_RETRIES", "10")))
        self._restart_window_seconds = max(30.0, float(os.getenv("REID_CAPTURE_RESTART_WINDOW_SECONDS", "300")))

        self._backend_root = Path(__file__).resolve().parents[2]
        self._repo_root = self._backend_root.parent
        self._capture_root = self._repo_root / "hikvision_local_capture"
        self._runtime_state_path = self._backend_root / "data" / "capture_runtime_state.json"
        self._config_audit_path = self._backend_root / "data" / "capture_config_audit.jsonl"
        self._runtime_config_path = self._backend_root / "data" / "capture_runtime_config.yaml"
        self._runtime_config_dir = self._backend_root / "data" / "capture_runtime_configs"
        self._script_path = self._capture_root / "capture_vmd_photos.py"
        self._config_source = "db://capture_settings"
        self._active_camera_id: str | None = None
        self._active_camera_ids: list[str] = []
        self._last_start_errors: list[str] = []
        loaded_state = self._load_persistent_desired_state()
        self._desired_running = bool(loaded_state.get("desired_running", False))
        loaded_ids = loaded_state.get("desired_camera_ids", [])
        if isinstance(loaded_ids, list):
            self._desired_camera_ids = {
                str(item).strip() for item in loaded_ids if isinstance(item, str) and str(item).strip()
            }

    def _load_persistent_desired_state(self) -> dict[str, Any]:
        path = self._runtime_state_path
        if not path.exists():
            return {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        if not isinstance(raw, dict):
            return {}
        return raw

    def _persist_desired_state_unlocked(self) -> None:
        payload = {
            "desired_running": bool(self._desired_running),
            "desired_camera_ids": sorted(self._desired_camera_ids),
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        path = self._runtime_state_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def _append_log(self, message: str, source: str = "control") -> None:
        item = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source": source,
            "line": message.strip(),
        }
        with self._lock:
            self._logs.append(item)

    def _refresh_process_state_unlocked(self) -> None:
        exited_camera_ids: list[str] = []
        for camera_id, process in list(self._processes.items()):
            exit_code = process.poll()
            if exit_code is None:
                continue
            exit_code_int = int(exit_code)
            self._last_exit_code = exit_code_int
            self._last_exit_code_by_camera[camera_id] = exit_code_int
            self._append_log(f"capture process exited camera_id={camera_id} code={exit_code_int}", source="control")
            self._processes.pop(camera_id, None)
            self._reader_threads.pop(camera_id, None)
            self._started_at_by_camera.pop(camera_id, None)
            self._command_by_camera.pop(camera_id, None)
            exited_camera_ids.append(camera_id)

        self._active_camera_ids = sorted(self._processes.keys())
        self._active_camera_id = self._active_camera_ids[0] if self._active_camera_ids else None
        for camera_id in exited_camera_ids:
            self._schedule_restart_unlocked(camera_id)

    def _restart_history_for_camera_unlocked(self, camera_id: str) -> deque[float]:
        history = self._restart_history_by_camera.get(camera_id)
        if history is None:
            history = deque(maxlen=256)
            self._restart_history_by_camera[camera_id] = history
        return history

    def _can_restart_unlocked(self, history: deque[float], now_ts: float) -> bool:
        while history and (now_ts - history[0]) > self._restart_window_seconds:
            history.popleft()
        return len(history) < self._restart_max_retries

    @staticmethod
    def _parse_channel_id_from_rtsp(rtsp_url: str) -> int | None:
        try:
            parsed = urlparse(rtsp_url)
        except Exception:
            return None
        path = (parsed.path or "").strip()
        if path:
            match = re.search(r"/channels/(\d+)", path, flags=re.IGNORECASE)
            if match:
                raw = int(match.group(1))
                if raw >= 100:
                    return max(1, raw // 100)
                return max(1, raw)
            nums = re.findall(r"\d+", path)
            if nums:
                raw = int(nums[-1])
                if raw >= 100:
                    return max(1, raw // 100)
                return max(1, raw)
        query = (parsed.query or "").strip()
        if query:
            for token in query.split("&"):
                key, _, value = token.partition("=")
                if key.lower() in {"channel", "channelid", "ch"} and value.isdigit():
                    return max(1, int(value))
        return None

    @staticmethod
    def _normalize_isapi_http_url(raw_url: str) -> str:
        text = (raw_url or "").strip()
        if not text:
            return ""
        try:
            parsed = urlparse(text)
        except Exception:
            return text
        scheme = (parsed.scheme or "").lower()
        path = parsed.path or ""
        if "/ISAPI/" not in path.upper():
            return text
        # ISAPI should use HTTP(S) service port, not RTSP 554.
        if scheme == "http" and parsed.port == 554:
            host = parsed.hostname or ""
            netloc = host
            if parsed.username:
                user = parsed.username
                if parsed.password:
                    netloc = f"{user}:{parsed.password}@{host}:80"
                else:
                    netloc = f"{user}@{host}:80"
            else:
                netloc = f"{host}:80"
            return urlunparse((parsed.scheme, netloc, parsed.path, parsed.params, parsed.query, parsed.fragment))
        if scheme == "https" and parsed.port == 554:
            host = parsed.hostname or ""
            netloc = host
            if parsed.username:
                user = parsed.username
                if parsed.password:
                    netloc = f"{user}:{parsed.password}@{host}:443"
                else:
                    netloc = f"{user}@{host}:443"
            else:
                netloc = f"{host}:443"
            return urlunparse((parsed.scheme, netloc, parsed.path, parsed.params, parsed.query, parsed.fragment))
        return text

    def _selected_camera_from_db(self, source_camera_id: str) -> dict[str, Any] | None:
        try:
            cameras = camera_config_store.load()
        except Exception as exc:  # noqa: BLE001
            self._append_log(f"load camera configs from db failed, fallback template config: {exc}", source="control")
            return None
        if not cameras:
            return None
        source_id = source_camera_id.strip()
        if source_id:
            for item in cameras:
                if str(item.get("id", "")).strip() == source_id:
                    return dict(item)
            return None
        for item in cameras:
            if bool(item.get("enabled", True)):
                return dict(item)
        return dict(cameras[0])

    def _merge_camera_from_db(
        self,
        template_config: dict[str, Any],
        override_source_camera_id: str | None = None,
    ) -> tuple[dict[str, Any], str | None]:
        camera_cfg_raw = template_config.get("camera")
        camera_cfg = dict(camera_cfg_raw) if isinstance(camera_cfg_raw, dict) else {}
        source_camera_id = (
            override_source_camera_id.strip()
            if isinstance(override_source_camera_id, str) and override_source_camera_id.strip()
            else str(camera_cfg.get("source_camera_id", "")).strip()
        )
        selected = self._selected_camera_from_db(source_camera_id)
        if selected is None:
            return camera_cfg, None

        rtsp_url = str(selected.get("rtsp_url", "")).strip()
        event_api_url = self._normalize_isapi_http_url(str(selected.get("event_api_url", "")).strip())
        snapshot_api_url = self._normalize_isapi_http_url(str(selected.get("snapshot_api_url", "")).strip())
        parsed = urlparse(rtsp_url) if rtsp_url else None
        parsed_event = urlparse(event_api_url) if event_api_url else None
        parsed_snapshot = urlparse(snapshot_api_url) if snapshot_api_url else None
        host = str(selected.get("host", "")).strip() or (parsed.hostname if parsed else None) or str(
            camera_cfg.get("host", "")
        ).strip()
        if not host:
            host = (
                (parsed_event.hostname if parsed_event else None)
                or (parsed_snapshot.hostname if parsed_snapshot else None)
                or ""
            )
        username = (
            str(selected.get("username", "")).strip()
            or (unquote(parsed.username) if parsed and parsed.username is not None else "")
            or (unquote(parsed_event.username) if parsed_event and parsed_event.username is not None else "")
            or (unquote(parsed_snapshot.username) if parsed_snapshot and parsed_snapshot.username is not None else "")
            or str(camera_cfg.get("username", "")).strip()
        )
        password = (
            ("" if selected.get("password") is None else str(selected.get("password", "")))
            or (unquote(parsed.password) if parsed and parsed.password is not None else "")
            or (unquote(parsed_event.password) if parsed_event and parsed_event.password is not None else "")
            or (unquote(parsed_snapshot.password) if parsed_snapshot and parsed_snapshot.password is not None else "")
            or ("" if camera_cfg.get("password") is None else str(camera_cfg.get("password", "")))
        )
        try:
            selected_channel = int(selected.get("channel_id", 0) or 0)
        except Exception:
            selected_channel = 0
        channel_id = (
            selected_channel
            or self._parse_channel_id_from_rtsp(rtsp_url)
            or int(camera_cfg.get("channel_id", 1) or 1)
        )
        try:
            selected_port = int(selected.get("port", 0) or 0)
        except Exception:
            selected_port = 0
        try:
            parsed_port = int(parsed.port) if parsed and parsed.port else 0
        except Exception:
            parsed_port = 0
        try:
            parsed_event_port = int(parsed_event.port) if parsed_event and parsed_event.port else 0
        except Exception:
            parsed_event_port = 0
        try:
            parsed_snapshot_port = int(parsed_snapshot.port) if parsed_snapshot and parsed_snapshot.port else 0
        except Exception:
            parsed_snapshot_port = 0
        camera_cfg["name"] = str(selected.get("id", "")).strip() or str(camera_cfg.get("name", "hikvision_camera"))
        camera_cfg["host"] = host
        camera_cfg["username"] = username
        camera_cfg["password"] = password
        camera_cfg["channel_id"] = max(1, channel_id)
        camera_cfg["port"] = parsed_event_port or parsed_snapshot_port or selected_port or parsed_port or int(
            camera_cfg.get("port", 80) or 80
        )
        if int(camera_cfg["port"]) == 554 and (
            "/ISAPI/" in str(event_api_url).upper()
            or "/ISAPI/" in str(snapshot_api_url).upper()
            or "/ISAPI/" in str(camera_cfg.get("stream_path", "")).upper()
            or "/ISAPI/" in str(camera_cfg.get("picture_path_template", "")).upper()
        ):
            camera_cfg["port"] = 80
        camera_cfg["scheme"] = (
            str(selected.get("scheme", "")).strip()
            or (str(parsed_event.scheme).strip() if parsed_event and parsed_event.scheme else "")
            or (str(parsed_snapshot.scheme).strip() if parsed_snapshot and parsed_snapshot.scheme else "")
            or str(camera_cfg.get("scheme", "http") or "http")
        )
        if event_api_url:
            camera_cfg["stream_url_override"] = event_api_url
        if snapshot_api_url:
            camera_cfg["picture_url_override"] = snapshot_api_url
        camera_cfg["source_camera_id"] = str(selected.get("id", "")).strip()
        return camera_cfg, str(selected.get("id", "")).strip() or None

    @staticmethod
    def _camera_channel_no(camera_cfg: dict[str, Any]) -> int:
        try:
            channel_id = int(camera_cfg.get("channel_id", 1) or 1)
        except Exception:
            channel_id = 1
        return max(1, channel_id) * 100 + 1

    def _camera_stream_url(self, camera_cfg: dict[str, Any]) -> str:
        override = str(camera_cfg.get("stream_url_override", "")).strip()
        if override:
            return self._normalize_isapi_http_url(override.format(channel_no=self._camera_channel_no(camera_cfg)))
        scheme = str(camera_cfg.get("scheme", "http") or "http").strip() or "http"
        host = str(camera_cfg.get("host", "")).strip()
        port = int(camera_cfg.get("port", 80) or 80)
        stream_path = str(
            camera_cfg.get("stream_path", "/ISAPI/Event/notification/alertStream")
            or "/ISAPI/Event/notification/alertStream"
        )
        return self._normalize_isapi_http_url(f"{scheme}://{host}:{port}{stream_path}")

    def _preflight_camera_connectivity_unlocked(self, camera_cfg: dict[str, Any]) -> None:
        stream_url = self._camera_stream_url(camera_cfg)
        username = str(camera_cfg.get("username", "")).strip()
        password = "" if camera_cfg.get("password") is None else str(camera_cfg.get("password", ""))
        auth: httpx.Auth | None = None
        if username and password:
            auth = httpx.DigestAuth(username, password)
        timeout = httpx.Timeout(connect=3.0, read=5.0, write=3.0, pool=3.0)
        try:
            with httpx.Client(auth=auth, timeout=timeout, follow_redirects=True) as client:
                with client.stream("GET", stream_url) as resp:
                    resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"camera stream preflight failed url={stream_url}: {exc}") from exc

    @staticmethod
    def _safe_camera_token(camera_id: str) -> str:
        token = re.sub(r"[^a-zA-Z0-9._-]+", "_", str(camera_id or "").strip())
        token = token.strip("._-")
        return token or "camera"

    def _runtime_config_path_for_camera(self, camera_id: str) -> Path:
        safe = self._safe_camera_token(camera_id)
        return (self._runtime_config_dir / f"{safe}.yaml").resolve()

    def _enabled_camera_ids(self) -> list[str]:
        cameras = camera_config_store.load()
        ids: list[str] = []
        for item in cameras:
            camera_id = str(item.get("id", "")).strip()
            if not camera_id:
                continue
            if bool(item.get("enabled", True)):
                ids.append(camera_id)
        seen: set[str] = set()
        out: list[str] = []
        for item in ids:
            if item in seen:
                continue
            seen.add(item)
            out.append(item)
        return out

    def _build_runtime_config_unlocked(
        self,
        override_source_camera_id: str,
        runtime_path: Path,
    ) -> tuple[Path, str, str]:
        template = self.get_config()
        runtime = copy.deepcopy(template)
        camera_cfg, active_camera_id = self._merge_camera_from_db(
            template,
            override_source_camera_id=override_source_camera_id,
        )
        if not active_camera_id:
            raise ValueError(f"camera not found in db: {override_source_camera_id}")
        runtime["camera"] = camera_cfg

        host = str(camera_cfg.get("host", "")).strip()
        event_api_url = str(camera_cfg.get("stream_url_override", "")).strip()
        snapshot_api_url = str(camera_cfg.get("picture_url_override", "")).strip()
        if not host and not (event_api_url and snapshot_api_url):
            raise ValueError(
                "capture camera credentials are incomplete; "
                "set camera.source_camera_id to a DB camera with API urls "
                "or configure camera host/path in template config"
            )
        self._preflight_camera_connectivity_unlocked(camera_cfg)

        text = yaml.safe_dump(runtime, allow_unicode=True, sort_keys=False)
        return runtime_path, active_camera_id, text

    def _spawn_process_unlocked(self, override_source_camera_id: str) -> str:
        self._ensure_paths()
        target_camera_id = override_source_camera_id.strip()
        if not target_camera_id:
            raise ValueError("camera_id is required to spawn capture process")
        runtime_path = self._runtime_config_path_for_camera(target_camera_id)
        runtime_cfg, active_camera_id, runtime_text = self._build_runtime_config_unlocked(
            override_source_camera_id=target_camera_id,
            runtime_path=runtime_path,
        )
        cmd = [sys.executable, "-u", str(self._script_path), "--config", "-", "--config-base", str(runtime_cfg)]
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["REID_CONFIG_FILE"] = str((self._backend_root / "config.yaml").resolve())
        process = subprocess.Popen(
            cmd,
            cwd=str(self._repo_root),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            bufsize=1,
            env=env,
        )
        try:
            if process.stdin is None:
                raise RuntimeError("capture process stdin unavailable")
            process.stdin.write(runtime_text)
            process.stdin.close()
        except Exception:
            try:
                process.kill()
            finally:
                raise
        self._processes[active_camera_id] = process
        self._started_at_by_camera[active_camera_id] = datetime.now(timezone.utc)
        self._command_by_camera[active_camera_id] = cmd
        self._runtime_config_by_camera[active_camera_id] = runtime_cfg
        self._last_exit_code_by_camera[active_camera_id] = None
        self._restart_pending_by_camera.discard(active_camera_id)
        self._active_camera_ids = sorted(self._processes.keys())
        self._active_camera_id = self._active_camera_ids[0] if self._active_camera_ids else None
        self._append_log(
            f"capture process started camera_id={active_camera_id}",
            source="control",
        )
        thread = threading.Thread(target=self._reader_loop, args=(process, active_camera_id), daemon=True)
        self._reader_threads[active_camera_id] = thread
        thread.start()
        return active_camera_id

    def _restart_worker(self, camera_id: str, delay: float) -> None:
        time.sleep(max(0.1, delay))
        with self._lock:
            self._restart_pending_by_camera.discard(camera_id)
            if not self._desired_running:
                return
            if self._desired_camera_ids and camera_id not in self._desired_camera_ids:
                return
            process = self._processes.get(camera_id)
            if process is not None and process.poll() is None:
                return
            now_ts = time.time()
            history = self._restart_history_for_camera_unlocked(camera_id)
            if not self._can_restart_unlocked(history, now_ts):
                self._append_log(
                    (
                        "capture auto-restart disabled for camera: "
                        f"camera_id={camera_id} retries exceeded "
                        f"{self._restart_max_retries}/{int(self._restart_window_seconds)}s"
                    ),
                    source="control",
                )
                self._desired_camera_ids.discard(camera_id)
                if not self._desired_camera_ids and not self._processes:
                    self._desired_running = False
                self._persist_desired_state_unlocked()
                return
            history.append(now_ts)
            self._restart_count += 1
            self._restart_count_by_camera[camera_id] = self._restart_count_by_camera.get(camera_id, 0) + 1
            try:
                actual_camera_id = self._spawn_process_unlocked(camera_id)
                self._append_log(
                    (
                        "capture process restarted "
                        f"camera_id={actual_camera_id} count={self._restart_count_by_camera.get(actual_camera_id, 0)}"
                    ),
                    source="control",
                )
            except Exception as exc:  # noqa: BLE001
                self._append_log(f"capture process restart failed camera_id={camera_id}: {exc}", source="control")
                self._schedule_restart_unlocked(camera_id)

    def _schedule_restart_unlocked(self, camera_id: str) -> None:
        if not self._desired_running or not self._auto_restart_enabled:
            return
        if self._desired_camera_ids and camera_id not in self._desired_camera_ids:
            return
        if camera_id in self._restart_pending_by_camera:
            return
        self._restart_pending_by_camera.add(camera_id)
        self._append_log(
            (
                "capture process will restart "
                f"camera_id={camera_id} in {self._restart_delay_seconds:.1f}s"
            ),
            source="control",
        )
        thread = threading.Thread(
            target=self._restart_worker,
            args=(camera_id, self._restart_delay_seconds),
            daemon=True,
        )
        thread.start()

    def _reader_loop(self, process: subprocess.Popen[str], camera_id: str) -> None:
        try:
            if process.stdout is None:
                return
            for line in process.stdout:
                clean = line.rstrip("\n")
                if not clean:
                    continue
                self._append_log(clean, source=f"capture:{camera_id}")
        except Exception as exc:  # noqa: BLE001
            self._append_log(f"log reader failed camera_id={camera_id}: {exc}", source="control")
        finally:
            with self._lock:
                self._refresh_process_state_unlocked()

    def _ensure_paths(self) -> None:
        if not self._script_path.exists():
            raise FileNotFoundError(f"capture script not found: {self._script_path}")

    def _default_capture_config(self) -> dict[str, Any]:
        return {
            "camera": {
                "name": "camera",
                "source_camera_id": "",
                "host": "",
                "port": 80,
                "scheme": "http",
                "username": "",
                "password": "",
                "channel_id": 1,
                "stream_path": "/ISAPI/Event/notification/alertStream",
                "picture_path_template": "/ISAPI/Streaming/channels/{channel_no}/picture",
                "stream_url_override": "",
                "picture_url_override": "",
                "request_timeout_seconds": 10,
                "snapshot_retry_count": 2,
                "snapshot_retry_delay_ms": 120,
                "cooldown_seconds": 2,
                "burst_count": 4,
                "max_capture_attempts": 36,
                "burst_interval_ms": 180,
            },
            "detector": {
                "mode": "auto",
                "yolo_model_path": "models/yolov8n.onnx",
                "yolo_input_size": 640,
                "confidence_threshold": 0.2,
                "nms_threshold": 0.45,
                "reid_mode": "auto",
                "reid_model_path": "backend/models/person_reid.onnx",
                "reid_input_width": 128,
                "reid_input_height": 256,
                "hog_hit_threshold": 0.0,
                "min_person_confidence": 0.2,
                "min_person_area_ratio": 0.006,
                "enable_tiled_detection": True,
                "tiled_detection_grid_size": 2,
                "tiled_detection_overlap_ratio": 0.25,
                "tiled_detection_trigger_area_ratio": 0.03,
                "enable_bbox_stabilization": True,
                "bbox_stabilize_alpha": 0.72,
                "bbox_stabilize_iou_threshold": 0.18,
                "bbox_stabilize_center_distance_ratio": 0.22,
                "enable_rider_rescue": True,
                "rider_rescue_confidence": 0.1,
                "rider_rescue_area_ratio": 0.002,
            },
            "quality": {
                "min_laplacian_var": 50,
                "min_brightness": 25,
                "max_brightness": 230,
                "min_contrast_std": 14,
            },
            "dedup": {"hamming_threshold": 5},
            "reliability": {
                "min_consecutive_vmd_active": 2,
                "active_window_seconds": 3,
                "same_target_suppress_seconds": 120,
                "same_target_embedding_similarity": 0.94,
                "same_target_hash_hamming_threshold": 4,
                "same_target_area_ratio_delta": 0.08,
            },
            "color": {
                "enable_normalization": True,
                "target_brightness": 128,
                "night_brightness_threshold": 70,
            },
            "output": {
                "dir": "photos",
                "save_to_db": True,
                "save_local_image": False,
                "local_fallback_on_db_error": True,
                "save_sidecar_json": False,
                "save_metadata_jsonl": False,
                "metadata_jsonl_name": "metadata.jsonl",
            },
            "logging": {"verbose_events": False},
        }

    def _load_config_from_db(self) -> dict[str, Any]:
        pool = None
        conn = None
        try:
            pool = db_pool.get_pool()
            conn = pool.getconn()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT config_json
                    FROM capture_settings
                    WHERE config_key = 'default'
                    LIMIT 1
                    """
                )
                row = cur.fetchone()
            if not row or not isinstance(row[0], dict):
                return self._default_capture_config()
            return dict(row[0])
        finally:
            if pool is not None and conn is not None:
                pool.putconn(conn)

    def _save_config_to_db(self, config: dict[str, Any]) -> None:
        pool = None
        conn = None
        try:
            pool = db_pool.get_pool()
            conn = pool.getconn()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO capture_settings(config_key, config_json, updated_at)
                    VALUES ('default', %s::jsonb, NOW())
                    ON CONFLICT (config_key)
                    DO UPDATE SET config_json = EXCLUDED.config_json, updated_at = NOW()
                    """,
                    (json.dumps(config, ensure_ascii=False),),
                )
            conn.commit()
        except Exception:
            if conn is not None:
                conn.rollback()
            raise
        finally:
            if pool is not None and conn is not None:
                pool.putconn(conn)

    def get_config(self) -> dict[str, Any]:
        self._ensure_paths()
        raw = self._load_config_from_db()
        if not isinstance(raw, dict):
            raise ValueError("capture config in db must be a JSON mapping")
        return raw

    def _flatten_scalars(self, value: Any, prefix: str = "") -> dict[str, Any]:
        out: dict[str, Any] = {}
        if isinstance(value, dict):
            for key, sub in value.items():
                if not isinstance(key, str):
                    continue
                next_prefix = f"{prefix}.{key}" if prefix else key
                out.update(self._flatten_scalars(sub, next_prefix))
            return out
        if isinstance(value, list):
            out[prefix] = value
            return out
        out[prefix] = value
        return out

    def _append_config_audit(self, actor: str, before: dict[str, Any], after: dict[str, Any]) -> None:
        before_flat = self._flatten_scalars(before)
        after_flat = self._flatten_scalars(after)
        keys = sorted(set(before_flat.keys()) | set(after_flat.keys()))
        changed_paths = [key for key in keys if before_flat.get(key) != after_flat.get(key)]
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "actor": (actor or "system").strip() or "system",
            "changed_count": len(changed_paths),
            "changed_paths": changed_paths[:200],
        }
        self._config_audit_path.parent.mkdir(parents=True, exist_ok=True)
        with self._config_audit_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False))
            f.write("\n")

    def save_config(self, config: dict[str, Any], actor: str = "system") -> None:
        if not isinstance(config, dict):
            raise ValueError("config payload must be a mapping")
        before: dict[str, Any] = {}
        try:
            before = self.get_config()
        except Exception:
            before = {}
        self._save_config_to_db(config)
        try:
            self._append_config_audit(actor=actor, before=before, after=config)
        except Exception as exc:  # noqa: BLE001
            self._append_log(f"capture config audit write failed: {exc}", source="control")
        self._append_log("capture config updated", source="control")

    def config_audit_items(self, limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 500))
        if not self._config_audit_path.exists():
            return []
        lines: deque[str] = deque(maxlen=limit)
        with self._config_audit_path.open("r", encoding="utf-8") as f:
            for line in f:
                clean = line.strip()
                if clean:
                    lines.append(clean)
        out: list[dict[str, Any]] = []
        for line in reversed(lines):
            try:
                item = json.loads(line)
            except Exception:
                continue
            if isinstance(item, dict):
                out.append(item)
        return out

    def _output_dir_from_config(self, config: dict[str, Any]) -> Path:
        output = config.get("output", {})
        if not isinstance(output, dict):
            output = {}
        raw_dir = str(output.get("dir", "photos"))
        output_dir = Path(raw_dir)
        if not output_dir.is_absolute():
            output_dir = self._capture_root / output_dir
        return output_dir.resolve()

    def get_output_dir(self) -> Path:
        config = self.get_config()
        return self._output_dir_from_config(config)

    def get_metadata_jsonl_path(self) -> Path | None:
        config = self.get_config()
        output = config.get("output", {})
        if not isinstance(output, dict):
            output = {}
        enabled = bool(output.get("save_metadata_jsonl", True))
        if not enabled:
            return None
        jsonl_name = str(output.get("metadata_jsonl_name", "metadata.jsonl")).strip() or "metadata.jsonl"
        return self._output_dir_from_config(config) / jsonl_name

    def read_metadata_items(self, limit: int = 500) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 20000))
        jsonl_path = self.get_metadata_jsonl_path()
        if jsonl_path is None or not jsonl_path.exists():
            return []

        lines: deque[str] = deque(maxlen=limit)
        with jsonl_path.open("r", encoding="utf-8") as f:
            for line in f:
                clean = line.strip()
                if clean:
                    lines.append(clean)

        result: list[dict[str, Any]] = []
        for line in reversed(lines):
            try:
                item = json.loads(line)
            except Exception:
                continue
            if not isinstance(item, dict):
                continue
            result.append(item)
        return result

    def count_metadata_items(self) -> int:
        jsonl_path = self.get_metadata_jsonl_path()
        if jsonl_path is None or not jsonl_path.exists():
            return 0
        count = 0
        with jsonl_path.open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    count += 1
        return count

    @staticmethod
    def _terminate_process(process: subprocess.Popen[str]) -> None:
        try:
            process.terminate()
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    def start(self, camera_id: str | None = None) -> dict[str, Any]:
        requested_camera_id = (camera_id or "").strip()
        to_stop: list[subprocess.Popen[str]] = []
        to_start: list[str] = []
        target_camera_ids: list[str] = []
        with self._lock:
            self._refresh_process_state_unlocked()
            self._last_start_errors = []
            if requested_camera_id:
                target_camera_ids = [requested_camera_id]
            else:
                target_camera_ids = self._enabled_camera_ids()
            if not target_camera_ids:
                raise ValueError("no enabled camera found in db")

            self._desired_running = True
            self._desired_camera_ids = {item.strip() for item in target_camera_ids if item.strip()}
            self._persist_desired_state_unlocked()

            running_ids = {
                camera for camera, process in self._processes.items() if process.poll() is None
            }
            if running_ids == self._desired_camera_ids and running_ids:
                return self.status()
            stop_ids = [camera for camera in running_ids if camera not in self._desired_camera_ids]
            to_start = [camera for camera in target_camera_ids if camera not in running_ids]
            to_stop = [self._processes[camera] for camera in stop_ids if camera in self._processes]

        for process in to_stop:
            self._terminate_process(process)

        errors: list[str] = []
        with self._lock:
            self._refresh_process_state_unlocked()
            for camera in to_start:
                try:
                    self._spawn_process_unlocked(camera)
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{camera}: {exc}")
            self._active_camera_ids = sorted(self._processes.keys())
            self._active_camera_id = self._active_camera_ids[0] if self._active_camera_ids else None

            if errors:
                self._last_start_errors = list(errors)
                self._append_log(
                    "capture start partial failures: " + " | ".join(errors[:8]),
                    source="control",
                )
            if errors and not self._processes:
                self._desired_running = False
                self._desired_camera_ids.clear()
                self._persist_desired_state_unlocked()
                raise RuntimeError("; ".join(errors))
            return self.status()

    def stop(self) -> dict[str, Any]:
        to_stop: list[subprocess.Popen[str]] = []
        with self._lock:
            self._desired_running = False
            self._desired_camera_ids.clear()
            self._persist_desired_state_unlocked()
            self._refresh_process_state_unlocked()
            to_stop = [process for process in self._processes.values() if process.poll() is None]
            if not to_stop:
                return self.status()

        for process in to_stop:
            self._terminate_process(process)
        with self._lock:
            self._refresh_process_state_unlocked()
            self._append_log("capture process stopped", source="control")
            return self.status()

    def restart(self, camera_id: str | None = None) -> dict[str, Any]:
        self.stop()
        return self.start(camera_id=camera_id)

    def restore_if_needed(self) -> dict[str, Any]:
        to_start: list[str] = []
        with self._lock:
            self._refresh_process_state_unlocked()
            if not self._desired_running:
                return self.status()
            if not self._desired_camera_ids:
                try:
                    enabled_ids = self._enabled_camera_ids()
                except Exception:
                    enabled_ids = []
                self._desired_camera_ids = {item.strip() for item in enabled_ids if item.strip()}
                self._persist_desired_state_unlocked()
            if not self._desired_camera_ids:
                self._desired_running = False
                self._persist_desired_state_unlocked()
                return self.status()
            running_ids = {
                camera for camera, process in self._processes.items() if process.poll() is None
            }
            to_start = sorted(self._desired_camera_ids - running_ids)

        for camera in to_start:
            with self._lock:
                try:
                    self._spawn_process_unlocked(camera)
                    self._append_log(
                        f"capture process restored from persistent desired state camera_id={camera}",
                        source="control",
                    )
                except Exception as exc:  # noqa: BLE001
                    self._append_log(f"capture restore failed camera_id={camera}: {exc}", source="control")
            time.sleep(0.02)
        with self._lock:
            return self.status()

    def shutdown(self) -> None:
        try:
            self.stop()
        except Exception:  # noqa: BLE001
            self._logger.exception("failed to stop capture process during shutdown")

    def status(self) -> dict[str, Any]:
        with self._lock:
            self._refresh_process_state_unlocked()
            running_workers: list[dict[str, Any]] = []
            for camera_id in sorted(self._processes.keys()):
                process = self._processes.get(camera_id)
                if process is None:
                    continue
                worker_running = process.poll() is None
                running_workers.append(
                    {
                        "camera_id": camera_id,
                        "running": worker_running,
                        "pid": process.pid if worker_running else None,
                        "started_at": self._started_at_by_camera.get(camera_id),
                        "last_exit_code": self._last_exit_code_by_camera.get(camera_id),
                        "restart_pending": camera_id in self._restart_pending_by_camera,
                        "restart_count": self._restart_count_by_camera.get(camera_id, 0),
                        "runtime_config_path": str(self._runtime_config_by_camera.get(camera_id, self._runtime_config_path)),
                        "command": list(self._command_by_camera.get(camera_id, [])),
                    }
                )
            primary = running_workers[0] if running_workers else None
            return {
                "running": bool(running_workers),
                "desired_running": self._desired_running,
                "pid": primary.get("pid") if isinstance(primary, dict) else None,
                "started_at": primary.get("started_at") if isinstance(primary, dict) else None,
                "last_exit_code": self._last_exit_code,
                "auto_restart_enabled": self._auto_restart_enabled,
                "restart_pending": bool(self._restart_pending_by_camera),
                "restart_count": self._restart_count,
                "script_path": str(self._script_path),
                "config_path": self._config_source,
                "runtime_config_path": (
                    str(primary.get("runtime_config_path"))
                    if isinstance(primary, dict)
                    else str(self._runtime_config_path)
                ),
                "active_camera_id": self._active_camera_id,
                "active_camera_ids": list(self._active_camera_ids),
                "desired_camera_ids": sorted(self._desired_camera_ids),
                "pending_camera_ids": sorted(set(self._desired_camera_ids) - set(self._active_camera_ids)),
                "worker_count": len(running_workers),
                "workers": running_workers,
                "start_errors": list(self._last_start_errors),
                "command": list(primary.get("command", [])) if isinstance(primary, dict) else [],
            }

    def logs(self, limit: int = 200) -> list[dict[str, str]]:
        with self._lock:
            items = list(self._logs)
        if limit <= 0:
            return []
        return items[-limit:]

    def recent_items(self, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 500))
        return self.read_metadata_items(limit=limit)

    def read_photo(self, image_path: str) -> tuple[bytes, str]:
        if not image_path.strip():
            raise FileNotFoundError("empty image path")
        config = self.get_config()
        output_dir = self._output_dir_from_config(config).resolve()
        candidate = Path(image_path).expanduser()
        if not candidate.is_absolute():
            candidate = output_dir / candidate
        resolved = candidate.resolve()
        if output_dir not in resolved.parents and resolved != output_dir:
            raise PermissionError("image path is outside capture output directory")
        if not resolved.exists() or not resolved.is_file():
            raise FileNotFoundError(f"image not found: {resolved}")
        data = resolved.read_bytes()
        guessed, _ = mimetypes.guess_type(str(resolved))
        media_type = guessed or "application/octet-stream"
        return data, media_type


capture_control_service = CaptureControlService()
