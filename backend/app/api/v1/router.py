from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.routes import admin, alerts, analytics, auth, cameras, capture, search, status

api_router = APIRouter()
api_router.include_router(auth.router, prefix="/auth", tags=["Auth"])
api_router.include_router(status.router, tags=["System"])
api_router.include_router(analytics.router, tags=["Analytics"])
api_router.include_router(search.router, tags=["Search"])
api_router.include_router(alerts.router, tags=["Alerts"])
api_router.include_router(admin.router, tags=["Admin"])
api_router.include_router(cameras.router, tags=["Cameras"])
api_router.include_router(capture.router, tags=["Capture"])
