from __future__ import annotations

from urllib.parse import quote
from urllib.parse import urlparse

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response

from app.api.v1.deps import AuthUser, require_permission
from app.models.schemas import (
    CameraRoiConfigRequest,
    CameraRoiConfigResponse,
    CameraRoiTestResponse,
    CameraSourceConfigItem,
    CameraSourceConfigListResponse,
    CameraSourceConfigUpdateRequest,
)
from app.services.camera_config_store import camera_config_store
from app.services.capture_control_service import capture_control_service
from app.services.camera_recognition_service import camera_recognition_service

router = APIRouter(prefix="/cameras")


def _seed_camera_from_capture_template_if_empty(items: list[dict[str, object]]) -> list[dict[str, object]]:
    if items:
        return items
    try:
        cfg = capture_control_service.get_config()
    except Exception:
        return items
    camera_cfg = cfg.get("camera", {}) if isinstance(cfg.get("camera"), dict) else {}
    host = str(camera_cfg.get("host", "")).strip()
    username = str(camera_cfg.get("username", "")).strip()
    password = "" if camera_cfg.get("password") is None else str(camera_cfg.get("password", ""))
    if not host or not username or not password:
        return items

    channel_id = int(camera_cfg.get("channel_id", 1) or 1)
    channel_no = max(1, channel_id) * 100 + 1
    camera_id = str(camera_cfg.get("source_camera_id", "")).strip() or str(camera_cfg.get("name", "")).strip() or host
    camera_name = str(camera_cfg.get("name", "")).strip() or camera_id
    port = int(camera_cfg.get("port", 80) or 80)
    scheme = str(camera_cfg.get("scheme", "http") or "http")
    rtsp_url = (
        f"rtsp://{quote(username, safe='')}:{quote(password, safe='')}@{host}:554/Streaming/Channels/{channel_no}"
    )
    stream_path = str(camera_cfg.get("stream_path", "/ISAPI/Event/notification/alertStream") or "/ISAPI/Event/notification/alertStream")
    picture_path_template = str(
        camera_cfg.get("picture_path_template", "/ISAPI/Streaming/channels/{channel_no}/picture")
        or "/ISAPI/Streaming/channels/{channel_no}/picture"
    )
    event_api_url = str(camera_cfg.get("stream_url_override", "")).strip() or (
        f"{scheme}://{host}:{port}{stream_path}"
    )
    snapshot_api_url = str(camera_cfg.get("picture_url_override", "")).strip() or (
        f"{scheme}://{host}:{port}{picture_path_template}"
    )
    seeded = [
        {
            "id": camera_id,
            "name": camera_name,
            "vendor": "hikvision_isapi",
            "rtsp_url": rtsp_url,
            "event_api_url": event_api_url,
            "snapshot_api_url": snapshot_api_url,
            "host": host,
            "port": port,
            "scheme": scheme,
            "username": username,
            "password": password,
            "channel_id": max(1, channel_id),
            "enabled": True,
        }
    ]
    try:
        camera_config_store.save(seeded)
    except Exception:
        return items
    return seeded


def _normalize_url(url: str) -> str:
    text = str(url or "").strip()
    if not text:
        return ""
    try:
        parsed = urlparse(text)
    except Exception:
        return text.lower().rstrip("/")
    scheme = (parsed.scheme or "").lower()
    host = (parsed.hostname or "").lower()
    path = (parsed.path or "").rstrip("/")
    query = parsed.query or ""
    port = parsed.port
    auth = ""
    if parsed.username:
        auth = parsed.username
        if parsed.password:
            auth = f"{auth}:{parsed.password}"
        auth += "@"
    netloc = f"{auth}{host}"
    if port:
        netloc += f":{port}"
    normalized = f"{scheme}://{netloc}{path}"
    if query:
        normalized += f"?{query}"
    return normalized


def _camera_source_key(item: dict[str, object]) -> str:
    event_api_url = _normalize_url(str(item.get("event_api_url", "")))
    snapshot_api_url = _normalize_url(str(item.get("snapshot_api_url", "")))
    if event_api_url or snapshot_api_url:
        return f"url|{event_api_url}|{snapshot_api_url}"
    scheme = str(item.get("scheme", "")).strip().lower()
    host = str(item.get("host", "")).strip().lower()
    port = int(item.get("port", 0) or 0)
    channel_id = int(item.get("channel_id", 0) or 0)
    username = str(item.get("username", "")).strip().lower()
    return f"host|{scheme}|{host}|{port}|{channel_id}|{username}"


def _validate_camera_source_uniqueness(items: list[dict[str, object]]) -> None:
    seen: dict[str, str] = {}
    for item in items:
        if not bool(item.get("enabled", True)):
            continue
        camera_id = str(item.get("id", "")).strip()
        if not camera_id:
            continue
        key = _camera_source_key(item)
        if not key or key in {"url||", "host||||0|0|"}:
            continue
        first = seen.get(key)
        if first:
            raise HTTPException(
                status_code=422,
                detail=(
                    "duplicate camera source detected for enabled cameras: "
                    f"{first} and {camera_id}; each camera must use unique device/channel source"
                ),
            )
        seen[key] = camera_id


@router.get("")
def list_cameras(_: object = Depends(require_permission("ingestion:status:read"))) -> dict[str, list[dict[str, object]]]:
    return {"items": camera_recognition_service.list_cameras()}


@router.get("/configs", response_model=CameraSourceConfigListResponse)
def get_camera_configs(_: object = Depends(require_permission("ingestion:status:read"))) -> CameraSourceConfigListResponse:
    try:
        items = camera_config_store.load()
        items = _seed_camera_from_capture_template_if_empty(items)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"failed to load camera configs from db: {exc}") from exc
    return CameraSourceConfigListResponse(source_path=camera_config_store.source_path, items=items)


@router.put("/configs", response_model=CameraSourceConfigListResponse)
def put_camera_configs(
    body: CameraSourceConfigUpdateRequest = Body(...),
    _: object = Depends(require_permission("camera:config:write")),
) -> CameraSourceConfigListResponse:
    items = [item.model_dump() for item in body.items]
    _validate_camera_source_uniqueness(items)
    camera_config_store.save(items)
    persisted = camera_config_store.load()
    camera_recognition_service.replace_camera_configs(persisted)
    return CameraSourceConfigListResponse(source_path=camera_config_store.source_path, items=persisted)


@router.post("/{camera_id}/test")
def test_camera(camera_id: str, _: object = Depends(require_permission("camera:test"))) -> dict[str, object]:
    return camera_recognition_service.test_camera(camera_id)


@router.get("/{camera_id}/recognize")
def recognize(
    camera_id: str,
    apply_roi: bool = Query(default=True),
    _: object = Depends(require_permission("ingestion:status:read")),
) -> dict[str, object]:
    result = camera_recognition_service.recognize(camera_id, apply_roi=apply_roi)
    if result.get("error") == "camera not configured":
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.get("/{camera_id}/snapshot")
def snapshot(
    camera_id: str,
    draw_boxes: bool = Query(default=True),
    apply_roi: bool = Query(default=True),
    _: object = Depends(require_permission("ingestion:status:read")),
) -> Response:
    jpeg = camera_recognition_service.snapshot(camera_id, draw_boxes=draw_boxes, apply_roi=apply_roi)
    if jpeg is None:
        raise HTTPException(status_code=404, detail="snapshot not available")
    return Response(content=jpeg, media_type="image/jpeg")


@router.get("/{camera_id}/roi", response_model=CameraRoiConfigResponse)
def get_camera_roi(
    camera_id: str,
    _: object = Depends(require_permission("ingestion:status:read")),
) -> CameraRoiConfigResponse:
    config = camera_recognition_service.get_roi_config(camera_id)
    if config is None:
        raise HTTPException(status_code=404, detail="camera not configured")
    return CameraRoiConfigResponse(**config)


@router.put("/{camera_id}/roi", response_model=CameraRoiConfigResponse)
def put_camera_roi(
    camera_id: str,
    body: CameraRoiConfigRequest = Body(...),
    user: AuthUser = Depends(require_permission("camera:roi:write")),
) -> CameraRoiConfigResponse:
    config = camera_recognition_service.update_roi_config(
        camera_id=camera_id,
        include=[[[p.x, p.y] for p in polygon.points] for polygon in body.include],
        exclude=[[[p.x, p.y] for p in polygon.points] for polygon in body.exclude],
        updated_by=user.username,
    )
    if config is None:
        raise HTTPException(status_code=404, detail="camera not configured")
    return CameraRoiConfigResponse(**config)


@router.get("/{camera_id}/roi/test", response_model=CameraRoiTestResponse)
def test_camera_roi(
    camera_id: str,
    _: object = Depends(require_permission("ingestion:status:read")),
) -> CameraRoiTestResponse:
    result = camera_recognition_service.test_roi_filter(camera_id)
    if result.get("error") == "camera not configured":
        raise HTTPException(status_code=404, detail=result["error"])
    if result.get("error") == "no frame available":
        raise HTTPException(status_code=409, detail=result["error"])
    return CameraRoiTestResponse(**result)
