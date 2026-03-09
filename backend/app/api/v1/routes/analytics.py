from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.v1.deps import require_permission
from app.models.schemas import AnalyticsDashboardResponse
from app.services.analytics_service import analytics_service

router = APIRouter(prefix="/analytics")


@router.get("/dashboard", response_model=AnalyticsDashboardResponse)
def dashboard(
    range_start: datetime | None = Query(default=None),
    range_end: datetime | None = Query(default=None),
    granularity: str = Query(default="auto"),
    camera_id: str | None = Query(default=None),
    _: object = Depends(require_permission("system:status:read")),
) -> AnalyticsDashboardResponse:
    try:
        return analytics_service.build_dashboard(
            range_start=range_start,
            range_end=range_end,
            granularity=granularity,
            camera_id=camera_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"failed to build analytics dashboard: {exc}") from exc
