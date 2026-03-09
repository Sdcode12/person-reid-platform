from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


_BACKEND_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="REID_", extra="ignore")

    app_name: str = Field(default="person-reid-platform")
    app_env: str = Field(default="dev")
    app_timezone: str = Field(default="Asia/Shanghai")
    setup_completed: bool = Field(default=False)
    force_setup: bool = Field(default=False)
    cors_allow_origins: list[str] = Field(
        default_factory=lambda: [
            "http://127.0.0.1:5173",
            "http://localhost:5173",
            "http://127.0.0.1:3000",
            "http://localhost:3000",
        ]
    )

    jwt_secret: str = Field(default="change-me")
    jwt_algorithm: str = Field(default="HS256")
    token_expire_minutes: int = Field(default=480)
    auth_mode: str = Field(default="db_only")

    db_host: str = Field(default="127.0.0.1")
    db_port: int = Field(default=5432)
    db_name: str = Field(default="camera_reid")
    db_user: str = Field(default="postgres")
    db_password: str = Field(default="")
    db_minconn: int = Field(default=1)
    db_maxconn: int = Field(default=5)

    cameras: list[dict[str, object]] = Field(default_factory=list)
    stream_reconnect_interval: int = Field(default=10)
    stream_buffer_size: int = Field(default=1)
    ingestion_interval_seconds: int = Field(default=1)

    detector_mode: str = Field(default="auto")
    detector_hog_hit_threshold: float = Field(default=0.0)
    detector_confidence_threshold: float = Field(default=0.35)
    detector_nms_threshold: float = Field(default=0.45)
    detector_persist_tracks: bool = Field(default=True)
    yolo_model_path: str = Field(default="models/yolov8n.onnx")
    yolo_input_size: int = Field(default=640)
    yolo_person_class_ids: list[int] = Field(default_factory=lambda: [0])
    snapshot_dir: str = Field(default="snapshots")


def config_file_path() -> Path:
    raw = os.getenv("REID_CONFIG_FILE", "config.yaml").strip() or "config.yaml"
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = (_BACKEND_ROOT / path).resolve()
    return path


def read_raw_config() -> dict[str, Any]:
    path = config_file_path()
    if not path.exists():
        return {}

    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    if not isinstance(raw, dict):
        return {}
    return raw


def write_raw_config(raw: dict[str, Any]) -> Path:
    path = config_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(raw, f, allow_unicode=True, sort_keys=False)
    return path


def _settings_map_from_raw(raw: dict[str, Any]) -> dict[str, Any]:
    detector_cfg = raw.get("detector", {})
    return {
        "app_name": raw.get("app", {}).get("name"),
        "app_env": raw.get("app", {}).get("env"),
        "app_timezone": raw.get("app", {}).get("timezone"),
        "setup_completed": raw.get("app", {}).get("setup_completed"),
        "force_setup": raw.get("app", {}).get("force_setup"),
        "cors_allow_origins": raw.get("app", {}).get("cors_allow_origins"),
        "jwt_secret": raw.get("security", {}).get("jwt_secret"),
        "jwt_algorithm": raw.get("security", {}).get("jwt_algorithm"),
        "token_expire_minutes": raw.get("security", {}).get("token_expire_minutes"),
        "auth_mode": raw.get("security", {}).get("auth_mode"),
        "db_host": raw.get("database", {}).get("host"),
        "db_port": raw.get("database", {}).get("port"),
        "db_name": raw.get("database", {}).get("dbname"),
        "db_user": raw.get("database", {}).get("user"),
        "db_password": raw.get("database", {}).get("password"),
        "db_minconn": raw.get("database", {}).get("minconn"),
        "db_maxconn": raw.get("database", {}).get("maxconn"),
        "cameras": raw.get("cameras", []),
        "stream_reconnect_interval": raw.get("stream", {}).get("reconnect_interval"),
        "stream_buffer_size": raw.get("stream", {}).get("buffer_size"),
        "ingestion_interval_seconds": raw.get("ingestion", {}).get("interval_seconds"),
        "detector_mode": detector_cfg.get("mode"),
        "detector_hog_hit_threshold": detector_cfg.get("hog_hit_threshold"),
        "detector_confidence_threshold": detector_cfg.get("confidence_threshold"),
        "detector_nms_threshold": detector_cfg.get("nms_threshold"),
        "detector_persist_tracks": detector_cfg.get("persist_tracks"),
        "yolo_model_path": detector_cfg.get("yolo_model_path"),
        "yolo_input_size": detector_cfg.get("yolo_input_size"),
        "yolo_person_class_ids": detector_cfg.get("person_class_ids"),
        "snapshot_dir": raw.get("storage", {}).get("snapshot_dir"),
    }


def apply_runtime_config(raw: dict[str, Any]) -> None:
    for key, value in _settings_map_from_raw(raw).items():
        if value is not None:
            setattr(settings, key, value)


settings = Settings(**{k: v for k, v in _settings_map_from_raw(read_raw_config()).items() if v is not None})
