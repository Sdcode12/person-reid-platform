from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import hashlib

from app.db.pool import db_pool
from app.core.timezone import ensure_aware, parse_iso_datetime
from app.services.camera_recognition_service import camera_recognition_service
from app.services.capture_control_service import capture_control_service
from app.services.capture_metadata_repo import capture_metadata_repo


def _parse_dt(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return ensure_aware(value)
    return parse_iso_datetime(value)


def _file_size_bytes(root: Path) -> int:
    if not root.exists():
        return 0
    total = 0
    for path in root.rglob("*"):
        if path.is_file():
            try:
                total += path.stat().st_size
            except Exception:
                continue
    return total


def _mk_alert(level: str, source: str, message: str, created_at: datetime) -> dict[str, str]:
    digest = hashlib.md5(f"{level}|{source}|{message}".encode("utf-8")).hexdigest()[:12]
    return {
        "id": f"alert-{digest}",
        "level": level,
        "source": source,
        "message": message,
        "created_at": created_at.isoformat(),
    }


def build_alert_items(now: datetime | None = None) -> list[dict[str, str]]:
    ts = now or datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ensure_aware(ts)

    alerts: list[dict[str, str]] = []
    runtime = capture_control_service.status()
    if not runtime.get("running", False):
        alerts.append(_mk_alert("warning", "capture", "抓拍进程未运行", ts))

    last_exit_code = runtime.get("last_exit_code")
    if isinstance(last_exit_code, int) and last_exit_code != 0:
        alerts.append(_mk_alert("critical", "capture", f"抓拍进程异常退出: code={last_exit_code}", ts))

    cameras = camera_recognition_service.list_cameras()
    offline = [cam for cam in cameras if not bool(cam.get("online", False))]
    if offline:
        names = ", ".join(str(cam.get("camera_name", cam.get("camera_id", ""))) for cam in offline[:3])
        msg = f"离线摄像头 {len(offline)} 路: {names}" if names else f"离线摄像头 {len(offline)} 路"
        alerts.append(_mk_alert("warning", "ingestion", msg, ts))

    try:
        recent_logs = capture_control_service.logs(limit=220)
    except Exception:
        recent_logs = []
    for log in reversed(recent_logs[-40:]):
        line = str(log.get("line", ""))
        if "[stream] disconnected" in line:
            alerts.append(_mk_alert("warning", "capture", line, _parse_dt(log.get("timestamp")) or ts))
            break
        if "capture process exited code=" in line and "code=0" not in line:
            alerts.append(_mk_alert("critical", "capture", line, _parse_dt(log.get("timestamp")) or ts))
            break

    unique: dict[str, dict[str, str]] = {}
    for item in alerts:
        unique[item["id"]] = item
    items = list(unique.values())
    items.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    return items


def build_admin_overview(now: datetime | None = None) -> dict[str, int | float | str]:
    ts = now or datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ensure_aware(ts)

    try:
        total_tracks = capture_metadata_repo.count_records()
        recent = capture_metadata_repo.query_items(
            limit=20000,
            camera_id=None,
            upper_color=None,
            lower_color=None,
            has_hat=None,
            image_mode=None,
            is_night=None,
            pose_hint=None,
            min_quality_score=None,
            time_start=None,
            time_end=None,
        )
    except Exception:
        try:
            total_tracks = capture_control_service.count_metadata_items()
            recent = capture_control_service.read_metadata_items(limit=20000)
        except Exception:
            total_tracks = 0
            recent = []
    today_tracks = 0
    latest_capture_time: datetime | None = None
    for item in recent:
        dt = _parse_dt(item.get("captured_at")) or _parse_dt(item.get("event_time"))
        if dt is None:
            continue
        if dt.date() == ts.date():
            today_tracks += 1
        if latest_capture_time is None or dt > latest_capture_time:
            latest_capture_time = dt

    try:
        output_dir = capture_control_service.get_output_dir()
        disk_bytes = _file_size_bytes(output_dir)
    except Exception:
        disk_bytes = 0
    disk_used_gb = round(disk_bytes / (1024 ** 3), 3)

    alert_items = build_alert_items(now=ts)
    alerts_open = len(alert_items)
    if latest_capture_time is None:
        note = "尚无抓拍数据"
    else:
        note = f"最近抓拍时间: {latest_capture_time.isoformat(timespec='seconds')}"

    return {
        "today_tracks": int(today_tracks),
        "total_tracks": int(total_tracks),
        "alerts_open": int(alerts_open),
        "disk_used_gb": float(disk_used_gb),
        "note": note,
    }


def build_ops_health(now: datetime | None = None) -> dict[str, Any]:
    ts = now or datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ensure_aware(ts)

    checks: list[dict[str, Any]] = []

    db_ok = db_pool.ping()
    checks.append(
        {
            "key": "db_connect",
            "label": "数据库连接",
            "level": "info" if db_ok else "critical",
            "ok": bool(db_ok),
            "value": "up" if db_ok else "down",
            "detail": None if db_ok else "PostgreSQL ping 失败",
        }
    )

    runtime = capture_control_service.status()
    running = bool(runtime.get("running", False))
    desired_running = bool(runtime.get("desired_running", False))
    capture_ok = running or not desired_running
    checks.append(
        {
            "key": "capture_runtime",
            "label": "抓拍进程",
            "level": "info" if capture_ok else "critical",
            "ok": capture_ok,
            "value": "running" if running else "stopped",
            "detail": f"desired_running={desired_running} pid={runtime.get('pid')}",
        }
    )

    restart_count = int(runtime.get("restart_count") or 0)
    restart_level = "info"
    if restart_count >= 20:
        restart_level = "warning"
    checks.append(
        {
            "key": "capture_restart_count",
            "label": "抓拍重启次数",
            "level": restart_level,
            "ok": restart_level == "info",
            "value": str(restart_count),
            "detail": "建议关注异常重启趋势" if restart_level != "info" else None,
        }
    )

    exit_code = runtime.get("last_exit_code")
    has_exit_error = isinstance(exit_code, int) and exit_code != 0
    checks.append(
        {
            "key": "capture_last_exit",
            "label": "最近退出码",
            "level": "critical" if has_exit_error else "info",
            "ok": not has_exit_error,
            "value": str(exit_code if exit_code is not None else 0),
            "detail": "最近一次进程退出非 0" if has_exit_error else None,
        }
    )

    cameras = camera_recognition_service.list_cameras()
    offline = [cam for cam in cameras if not bool(cam.get("online", False))]
    offline_count = len(offline)
    checks.append(
        {
            "key": "camera_offline",
            "label": "离线摄像头",
            "level": "warning" if offline_count > 0 else "info",
            "ok": offline_count == 0,
            "value": str(offline_count),
            "detail": None if offline_count == 0 else ", ".join(str(cam.get("camera_id", "")) for cam in offline[:5]),
        }
    )

    latest_capture_time: datetime | None = None
    try:
        recent = capture_metadata_repo.query_items(
            limit=1,
            camera_id=None,
            upper_color=None,
            lower_color=None,
            has_hat=None,
            image_mode=None,
            is_night=None,
            pose_hint=None,
            min_quality_score=None,
            time_start=None,
            time_end=None,
        )
        if recent:
            latest_capture_time = _parse_dt(recent[0].get("captured_at"))
    except Exception:
        try:
            local_items = capture_control_service.read_metadata_items(limit=1)
            if local_items:
                latest_capture_time = _parse_dt(local_items[0].get("captured_at")) or _parse_dt(local_items[0].get("event_time"))
        except Exception:
            latest_capture_time = None

    stale_minutes: float | None = None
    if latest_capture_time is not None:
        stale_minutes = max(0.0, (ts - latest_capture_time).total_seconds() / 60.0)
    stale_warn = stale_minutes is not None and stale_minutes > 20
    checks.append(
        {
            "key": "latest_capture_stale",
            "label": "最近抓拍时效",
            "level": "warning" if stale_warn else "info",
            "ok": not stale_warn,
            "value": f"{stale_minutes:.1f} min" if stale_minutes is not None else "n/a",
            "detail": None if stale_minutes is not None else "无抓拍数据",
        }
    )

    return {
        "generated_at": ts,
        "checks": checks,
    }
