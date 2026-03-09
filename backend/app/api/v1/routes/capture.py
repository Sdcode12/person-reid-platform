from __future__ import annotations

from functools import lru_cache
import os
from pathlib import Path
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query, Response

from app.api.v1.deps import AuthUser, require_permission
from app.core.detector import ReIDEmbeddingExtractor
from app.core.image_mode import normalize_image_mode
from app.core.timezone import ensure_aware, parse_iso_datetime
from app.models.schemas import (
    CaptureActionResponse,
    CaptureDeleteRequest,
    CaptureDeleteResponse,
    CaptureConfigResponse,
    CaptureConfigAuditResponse,
    CaptureConfigUpdateRequest,
    CaptureLogsResponse,
    CaptureModelStatusResponse,
    CaptureRecentResponse,
    CaptureRuntimeStatus,
)
from app.services.capture_control_service import capture_control_service
from app.services.capture_metadata_repo import capture_metadata_repo

router = APIRouter(prefix="/capture")


def _parse_iso_dt(raw: object) -> datetime | None:
    return parse_iso_datetime(raw)


def _safe_lower(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip().lower()


def _resolve_model_path(raw_path: str, config_path: Path, repo_root: Path, backend_root: Path) -> Path:
    path = Path((raw_path or "").strip())
    if path.is_absolute():
        return path
    candidates = [
        Path.cwd() / path,
        config_path.parent / path,
        repo_root / path,
        backend_root / path,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return (repo_root / path).resolve()


@lru_cache(maxsize=8)
def _probe_reid(mode: str, model_path: str, input_width: int, input_height: int) -> tuple[str, bool]:
    extractor = ReIDEmbeddingExtractor(
        mode=mode,
        model_path=model_path,
        input_width=input_width,
        input_height=input_height,
        output_dim=512,
    )
    return extractor.backend_name, bool(extractor.is_onnx_ready)


@router.get("/status", response_model=CaptureRuntimeStatus)
def get_capture_status(_: object = Depends(require_permission("capture:status:read"))) -> CaptureRuntimeStatus:
    return CaptureRuntimeStatus(**capture_control_service.status())


@router.get("/model-status", response_model=CaptureModelStatusResponse)
def get_model_status(_: object = Depends(require_permission("capture:status:read"))) -> CaptureModelStatusResponse:
    config = capture_control_service.get_config()
    detector_cfg = config.get("detector", {}) if isinstance(config.get("detector"), dict) else {}
    backend_root = Path(__file__).resolve().parents[4]
    repo_root = backend_root.parent
    runtime_cfg_path = Path(str(capture_control_service.status().get("runtime_config_path") or (backend_root / "data")))

    yolo_path_raw = str(detector_cfg.get("yolo_model_path", "models/yolov8n.onnx"))
    yolo_path = _resolve_model_path(yolo_path_raw, runtime_cfg_path, repo_root, backend_root)
    yolo_exists = yolo_path.exists()

    capture_reid_mode = str(detector_cfg.get("reid_mode", "auto") or "auto")
    capture_reid_path_raw = str(detector_cfg.get("reid_model_path", "backend/models/person_reid.onnx"))
    capture_reid_input_w = int(detector_cfg.get("reid_input_width", 128) or 128)
    capture_reid_input_h = int(detector_cfg.get("reid_input_height", 256) or 256)
    capture_reid_path = _resolve_model_path(capture_reid_path_raw, runtime_cfg_path, repo_root, backend_root)
    capture_reid_exists = capture_reid_path.exists()
    capture_backend, capture_ready = _probe_reid(
        capture_reid_mode,
        str(capture_reid_path),
        capture_reid_input_w,
        capture_reid_input_h,
    )

    search_reid_mode = str(os.getenv("REID_SEARCH_MODE", "auto") or "auto")
    search_reid_path_raw = str(os.getenv("REID_SEARCH_MODEL_PATH", "models/person_reid.onnx") or "models/person_reid.onnx")
    search_reid_input_w = int(os.getenv("REID_SEARCH_INPUT_WIDTH", "128") or "128")
    search_reid_input_h = int(os.getenv("REID_SEARCH_INPUT_HEIGHT", "256") or "256")
    search_reid_path = _resolve_model_path(search_reid_path_raw, runtime_cfg_path, repo_root, backend_root)
    search_reid_exists = search_reid_path.exists()
    search_backend, search_ready = _probe_reid(
        search_reid_mode,
        str(search_reid_path),
        search_reid_input_w,
        search_reid_input_h,
    )

    return CaptureModelStatusResponse(
        checked_at=datetime.now(timezone.utc),
        yolo_model_path=str(yolo_path),
        yolo_model_exists=bool(yolo_exists),
        reid_capture_mode=capture_reid_mode,
        reid_capture_model_path=str(capture_reid_path),
        reid_capture_model_exists=bool(capture_reid_exists),
        reid_capture_backend=capture_backend,
        reid_capture_ready=bool(capture_ready),
        reid_search_mode=search_reid_mode,
        reid_search_model_path=str(search_reid_path),
        reid_search_model_exists=bool(search_reid_exists),
        reid_search_backend=search_backend,
        reid_search_ready=bool(search_ready),
    )


@router.post("/start", response_model=CaptureActionResponse)
def start_capture(
    camera_id: str | None = Query(default=None),
    _: object = Depends(require_permission("capture:control")),
) -> CaptureActionResponse:
    try:
        status = capture_control_service.start(camera_id=camera_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"failed to start capture: {exc}") from exc
    return CaptureActionResponse(status=CaptureRuntimeStatus(**status))


@router.post("/stop", response_model=CaptureActionResponse)
def stop_capture(_: object = Depends(require_permission("capture:control"))) -> CaptureActionResponse:
    status = capture_control_service.stop()
    return CaptureActionResponse(status=CaptureRuntimeStatus(**status))


@router.post("/restart", response_model=CaptureActionResponse)
def restart_capture(
    camera_id: str | None = Query(default=None),
    _: object = Depends(require_permission("capture:control")),
) -> CaptureActionResponse:
    try:
        status = capture_control_service.restart(camera_id=camera_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"failed to restart capture: {exc}") from exc
    return CaptureActionResponse(status=CaptureRuntimeStatus(**status))


@router.get("/config", response_model=CaptureConfigResponse)
def get_capture_config(_: object = Depends(require_permission("capture:status:read"))) -> CaptureConfigResponse:
    try:
        config = capture_control_service.get_config()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"failed to read capture config: {exc}") from exc
    return CaptureConfigResponse(config_path=str(capture_control_service.status()["config_path"]), config=config)


@router.put("/config", response_model=CaptureConfigResponse)
def put_capture_config(
    body: CaptureConfigUpdateRequest,
    user: AuthUser = Depends(require_permission("capture:config:write")),
) -> CaptureConfigResponse:
    try:
        capture_control_service.save_config(body.config, actor=user.username)
        config = capture_control_service.get_config()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"failed to write capture config: {exc}") from exc
    return CaptureConfigResponse(config_path=str(capture_control_service.status()["config_path"]), config=config)


@router.get("/config-audit", response_model=CaptureConfigAuditResponse)
def get_capture_config_audit(
    limit: int = Query(default=80, ge=1, le=500),
    _: object = Depends(require_permission("capture:status:read")),
) -> CaptureConfigAuditResponse:
    try:
        return CaptureConfigAuditResponse(items=capture_control_service.config_audit_items(limit=limit))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"failed to read capture config audit: {exc}") from exc


@router.get("/logs", response_model=CaptureLogsResponse)
def get_capture_logs(
    limit: int = Query(default=200, ge=1, le=1000),
    _: object = Depends(require_permission("capture:status:read")),
) -> CaptureLogsResponse:
    return CaptureLogsResponse(items=capture_control_service.logs(limit=limit))


@router.get("/recent", response_model=CaptureRecentResponse)
def get_capture_recent(
    limit: int = Query(default=60, ge=1, le=500),
    _: object = Depends(require_permission("capture:status:read")),
) -> CaptureRecentResponse:
    db_error: Exception | None = None
    try:
        db_items = capture_metadata_repo.query_items(
            limit=limit,
            track_ids=None,
            image_paths=None,
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
        return CaptureRecentResponse(items=db_items)
    except Exception as exc:
        db_error = exc
    try:
        items = capture_control_service.recent_items(limit=limit)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"failed to read capture metadata: {exc}") from exc
    if items:
        return CaptureRecentResponse(items=items)
    if db_error is not None:
        raise HTTPException(status_code=503, detail=f"database query failed and no local fallback data: {db_error}") from db_error
    return CaptureRecentResponse(items=items)


@router.get("/query", response_model=CaptureRecentResponse)
def query_capture_items(
    limit: int = Query(default=100, ge=1, le=1000),
    scan_limit: int = Query(default=5000, ge=1, le=20000),
    camera_id: str | None = Query(default=None),
    upper_color: str | None = Query(default=None),
    lower_color: str | None = Query(default=None),
    has_hat: bool | None = Query(default=None),
    image_mode: str | None = Query(default=None),
    is_night: bool | None = Query(default=None),
    pose_hint: str | None = Query(default=None),
    min_quality_score: float | None = Query(default=None, ge=0.0, le=1.0),
    time_start: datetime | None = Query(default=None),
    time_end: datetime | None = Query(default=None),
    _: object = Depends(require_permission("capture:status:read")),
) -> CaptureRecentResponse:
    if time_start and time_start.tzinfo is None:
        time_start = ensure_aware(time_start)
    if time_end and time_end.tzinfo is None:
        time_end = ensure_aware(time_end)
    if time_start and time_end and time_start > time_end:
        raise HTTPException(status_code=422, detail="time_start must be <= time_end")

    query_camera = (camera_id or "").strip().lower()
    query_upper = _safe_lower(upper_color)
    query_lower = _safe_lower(lower_color)
    query_image_mode = normalize_image_mode(image_mode)
    query_pose = _safe_lower(pose_hint)

    db_error: Exception | None = None
    try:
        db_items = capture_metadata_repo.query_items(
            limit=limit,
            track_ids=None,
            image_paths=None,
            camera_id=camera_id,
            upper_color=upper_color,
            lower_color=lower_color,
            has_hat=has_hat,
            image_mode=query_image_mode,
            is_night=is_night,
            pose_hint=pose_hint,
            min_quality_score=min_quality_score,
            time_start=time_start,
            time_end=time_end,
        )
        return CaptureRecentResponse(items=db_items)
    except Exception as exc:
        # fallback to local jsonl filtering
        db_error = exc

    try:
        rows = capture_control_service.read_metadata_items(limit=scan_limit)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"failed to read capture metadata: {exc}") from exc

    result: list[dict[str, object]] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        row_camera = _safe_lower(item.get("camera_id"))
        if query_camera and row_camera != query_camera:
            continue

        row_upper = _safe_lower(item.get("upper_color"))
        if query_upper and row_upper != query_upper:
            continue
        row_lower = _safe_lower(item.get("lower_color"))
        if query_lower and row_lower != query_lower:
            continue

        if has_hat is not None:
            raw_hat = _safe_lower(item.get("has_hat"))
            row_hat = raw_hat in {"yes", "true", "1"}
            if raw_hat not in {"yes", "no", "true", "false", "1", "0"} or row_hat != has_hat:
                continue

        if query_image_mode:
            row_image_mode = normalize_image_mode(item.get("image_mode"))
            if row_image_mode is None:
                raw_night = item.get("is_night")
                if isinstance(raw_night, bool):
                    row_image_mode = "low_light_color" if raw_night else "color"
            if row_image_mode != query_image_mode:
                continue

        if is_night is not None:
            raw_night = item.get("is_night")
            if not isinstance(raw_night, bool) or raw_night != is_night:
                continue

        row_pose = _safe_lower(item.get("pose_hint"))
        if query_pose and row_pose != query_pose:
            continue

        if min_quality_score is not None:
            try:
                row_quality = float(item.get("quality_score"))
            except Exception:
                continue
            if row_quality < min_quality_score:
                continue

        row_time = _parse_iso_dt(item.get("captured_at")) or _parse_iso_dt(item.get("event_time"))
        if time_start and (row_time is None or row_time < time_start):
            continue
        if time_end and (row_time is None or row_time > time_end):
            continue

        result.append(item)
        if len(result) >= limit:
            break

    if not result and db_error is not None:
        raise HTTPException(status_code=503, detail=f"database query failed and no local fallback data: {db_error}") from db_error
    return CaptureRecentResponse(items=result)


@router.post("/delete", response_model=CaptureDeleteResponse)
def delete_capture_items(
    body: CaptureDeleteRequest,
    _: object = Depends(require_permission("capture:delete")),
) -> CaptureDeleteResponse:
    time_start = body.time_start
    time_end = body.time_end
    if time_start and time_start.tzinfo is None:
        time_start = ensure_aware(time_start)
    if time_end and time_end.tzinfo is None:
        time_end = ensure_aware(time_end)
    if time_start and time_end and time_start > time_end:
        raise HTTPException(status_code=422, detail="time_start must be <= time_end")

    if not body.track_ids and not body.image_paths:
        has_filter_criteria = any(
            value is not None and value != ""
            for value in (
                body.camera_id,
                body.upper_color,
                body.lower_color,
                body.image_mode,
                body.pose_hint,
                body.min_quality_score,
                time_start,
                time_end,
            )
        ) or body.has_hat is not None or body.is_night is not None
        if not has_filter_criteria:
            raise HTTPException(status_code=422, detail="delete requires selected items or active filters")

    try:
        stats = capture_metadata_repo.delete_items(
            track_ids=body.track_ids,
            image_paths=body.image_paths,
            camera_id=body.camera_id,
            upper_color=body.upper_color,
            lower_color=body.lower_color,
            has_hat=body.has_hat,
            image_mode=normalize_image_mode(body.image_mode),
            is_night=body.is_night,
            pose_hint=body.pose_hint,
            min_quality_score=body.min_quality_score,
            time_start=time_start,
            time_end=time_end,
            delete_local_files=body.delete_local_files,
            dry_run=body.dry_run,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"failed to delete capture metadata: {exc}") from exc

    mode = "selection" if body.track_ids or body.image_paths else "filters"
    return CaptureDeleteResponse(mode=mode, dry_run=body.dry_run, **stats)


@router.post("/sync-db")
def sync_capture_to_db(
    scan_limit: int = Query(default=5000, ge=1, le=200000),
    purge_local_images: bool = Query(default=True),
    _: object = Depends(require_permission("capture:control")),
) -> dict[str, int]:
    try:
        stats = capture_metadata_repo.sync_from_local(
            scan_limit=scan_limit,
            purge_local_images=purge_local_images,
        )
        stats["total_records"] = capture_metadata_repo.count_records()
        return stats
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"failed to sync capture metadata to db: {exc}") from exc


@router.get("/photo")
def get_capture_photo(
    image_path: str = Query(default="", min_length=0),
    track_id: int | None = Query(default=None, ge=1),
    _: object = Depends(require_permission("capture:status:read")),
) -> Response:
    if (not image_path or not image_path.strip()) and track_id is None:
        raise HTTPException(status_code=422, detail="image_path or track_id is required")
    try:
        db_photo = None
        db_error: Exception | None = None
        try:
            if track_id is not None:
                db_photo = capture_metadata_repo.get_photo_by_track_id(track_id=track_id)
            if db_photo is None and image_path and image_path.strip():
                db_photo = capture_metadata_repo.get_photo(image_path=image_path)
        except Exception as exc:
            db_photo = None
            db_error = exc
        if db_photo is not None:
            data, media_type = db_photo
            return Response(content=data, media_type=media_type)
        if image_path.strip().startswith("db://"):
            if db_error is not None:
                raise HTTPException(status_code=503, detail=f"database photo query failed: {db_error}") from db_error
            raise FileNotFoundError(f"image not found in database: {image_path}")
        if not image_path or not image_path.strip():
            raise FileNotFoundError(f"image not found in db by track_id={track_id}")
        data, media_type = capture_control_service.read_photo(image_path=image_path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"failed to read photo: {exc}") from exc
    return Response(content=data, media_type=media_type)
