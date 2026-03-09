from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.logging import configure_logging
from app.core.settings import settings
from app.db.migrations import run_db_migrations
from app.services.camera_config_store import camera_config_store
from app.services.capture_control_service import capture_control_service
from app.services.camera_recognition_service import camera_recognition_service
from app.core.logging import get_logger
from app.services.user_auth_service import user_auth_service

configure_logging()
logger = get_logger("app.main")


@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        migration_result = run_db_migrations()
        logger.info(
            "db migrations ready applied=%s skipped=%s",
            migration_result.applied,
            migration_result.skipped,
        )
    except Exception:
        logger.exception("failed to run db migrations at startup")
    try:
        user_auth_service.ensure_schema()
        logger.info("db user auth schema ready")
    except Exception:
        logger.exception("failed to ensure db user auth schema")
    try:
        db_cameras = camera_config_store.load()
    except Exception:
        logger.exception("failed to load camera configs from db, fallback to empty list")
        db_cameras = []
    camera_recognition_service.replace_camera_configs(db_cameras)
    camera_recognition_service.start()
    try:
        status = capture_control_service.restore_if_needed()
        if status.get("running"):
            logger.info("capture process restored at startup pid=%s", status.get("pid"))
    except Exception:
        logger.exception("failed to restore capture process at startup")
    try:
        yield
    finally:
        capture_control_service.shutdown()
        camera_recognition_service.stop()


app = FastAPI(title=settings.app_name, version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(api_router, prefix="/api/v1")


@app.get("/", tags=["System"])
def root() -> dict[str, str]:
    return {"service": settings.app_name, "docs": "/docs", "healthz": "/healthz"}


@app.get("/healthz", tags=["System"])
def healthz() -> dict[str, str]:
    return {"status": "ok"}
