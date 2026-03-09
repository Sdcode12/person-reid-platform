from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.v1.deps import require_permission
from app.services.monitoring_service import build_alert_items

router = APIRouter()


@router.get("/alerts")
def list_alerts(_: object = Depends(require_permission("alert:read"))) -> dict[str, list[dict[str, str]]]:
    try:
        return {"items": build_alert_items()}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"failed to load alerts: {exc}") from exc
