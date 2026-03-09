from __future__ import annotations

from datetime import datetime
import secrets
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from psycopg2 import connect

from app.api.v1.deps import require_permission
from app.core.settings import apply_runtime_config, config_file_path, read_raw_config, settings, write_raw_config
from app.db.pool import db_pool
from app.db.migrations import run_db_migrations
from app.models.schemas import (
    SetupDatabaseSummary,
    SetupDbConfigRequest,
    SetupDbTestResponse,
    SetupInitializeRequest,
    SetupInitializeResponse,
    SetupStatusResponse,
    StatusResponse,
)
from app.core.timezone import app_timezone
from app.services.user_auth_service import user_auth_service

router = APIRouter()


def _as_dict(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _normalize_setup_text(value: str) -> str:
    return (value or "").strip()


def _db_summary_from_raw(raw: dict[str, Any]) -> SetupDatabaseSummary:
    database = _as_dict(raw.get("database"))
    return SetupDatabaseSummary(
        host=str(database.get("host") or "").strip(),
        port=max(1, int(database.get("port") or 5432)),
        dbname=str(database.get("dbname") or "").strip(),
        user=str(database.get("user") or "").strip(),
        has_password=bool(str(database.get("password") or "")),
    )


def _db_connect_kwargs(
    *,
    host: str,
    port: int,
    dbname: str,
    user: str,
    password: str,
) -> dict[str, Any]:
    return {
        "host": host,
        "port": int(port),
        "dbname": dbname,
        "user": user,
        "password": password,
        "connect_timeout": 3,
    }


def _test_database_connection(
    *,
    host: str,
    port: int,
    dbname: str,
    user: str,
    password: str,
) -> SetupDbTestResponse:
    conn = None
    try:
        conn = connect(**_db_connect_kwargs(host=host, port=port, dbname=dbname, user=user, password=password))
        with conn.cursor() as cur:
            cur.execute("SELECT current_setting('server_version')")
            version_row = cur.fetchone()
            cur.execute("SELECT EXISTS(SELECT 1 FROM pg_extension WHERE extname = 'vector')")
            vector_row = cur.fetchone()
        version = str(version_row[0] or "").strip() if version_row else ""
        has_vector = bool(vector_row[0]) if vector_row else False
        return SetupDbTestResponse(
            ok=True,
            detail="database connection ok",
            db_version=version or None,
            pgvector_installed=has_vector,
        )
    except Exception as exc:  # noqa: BLE001
        return SetupDbTestResponse(ok=False, detail=str(exc))
    finally:
        if conn is not None:
            conn.close()


def _inspect_db_setup_state(summary: SetupDatabaseSummary) -> tuple[bool, bool, str | None]:
    if not summary.host or not summary.dbname or not summary.user:
        return False, False, None
    raw = read_raw_config()
    database = _as_dict(raw.get("database"))
    conn = None
    try:
        conn = connect(
            **_db_connect_kwargs(
                host=summary.host,
                port=summary.port,
                dbname=summary.dbname,
                user=summary.user,
                password=str(database.get("password") or settings.db_password or ""),
            )
        )
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    to_regclass('public.schema_migrations') IS NOT NULL,
                    to_regclass('public.users') IS NOT NULL
                """
            )
            table_row = cur.fetchone()
            schema_ready = bool(table_row[0]) and bool(table_row[1]) if table_row else False
            admin_exists = False
            if schema_ready:
                cur.execute(
                    """
                    SELECT EXISTS(
                        SELECT 1
                        FROM users
                        WHERE role = 'admin' AND is_active = TRUE
                    )
                    """
                )
                admin_row = cur.fetchone()
                admin_exists = bool(admin_row[0]) if admin_row else False
        return schema_ready, admin_exists, None
    except Exception as exc:  # noqa: BLE001
        return False, False, str(exc)
    finally:
        if conn is not None:
            conn.close()


def _next_setup_step(
    *,
    db_configured: bool,
    db_reachable: bool,
    schema_ready: bool,
    admin_exists: bool,
    setup_completed: bool,
) -> tuple[str, str | None]:
    if setup_completed:
        return "login", None
    if not db_configured:
        return "fill_database", "请先填写数据库连接信息。"
    if not db_reachable:
        return "test_database", "数据库尚未连接成功，请检查主机、端口和账号密码。"
    if not schema_ready:
        return "initialize_schema", "数据库已连通，但表结构尚未初始化。"
    if not admin_exists:
        return "create_admin", "数据库结构已就绪，请创建首个管理员账号。"
    return "finalize_setup", "初始化条件已满足，可以完成首装配置。"


def _build_setup_config(
    raw: dict[str, Any],
    body: SetupInitializeRequest,
    *,
    setup_completed: bool,
    force_setup: bool | None = None,
) -> dict[str, Any]:
    next_raw = dict(raw)
    app_cfg = _as_dict(next_raw.get("app"))
    security_cfg = _as_dict(next_raw.get("security"))
    database_cfg = _as_dict(next_raw.get("database"))

    if not _normalize_setup_text(str(app_cfg.get("name") or "")):
        app_cfg["name"] = settings.app_name
    if not _normalize_setup_text(str(app_cfg.get("env") or "")):
        app_cfg["env"] = settings.app_env
    if not _normalize_setup_text(str(app_cfg.get("timezone") or "")):
        app_cfg["timezone"] = settings.app_timezone
    if not isinstance(app_cfg.get("cors_allow_origins"), list) or not app_cfg.get("cors_allow_origins"):
        app_cfg["cors_allow_origins"] = list(settings.cors_allow_origins)
    app_cfg["setup_completed"] = bool(setup_completed)
    app_cfg["force_setup"] = bool(app_cfg.get("force_setup", False) if force_setup is None else force_setup)

    security_cfg["auth_mode"] = "db_only"
    if not _normalize_setup_text(str(security_cfg.get("jwt_algorithm") or "")):
        security_cfg["jwt_algorithm"] = settings.jwt_algorithm
    if not int(security_cfg.get("token_expire_minutes") or 0):
        security_cfg["token_expire_minutes"] = int(settings.token_expire_minutes)
    if str(security_cfg.get("jwt_secret") or "").strip() in {"", "change-me"}:
        security_cfg["jwt_secret"] = secrets.token_urlsafe(32)

    database_cfg["host"] = _normalize_setup_text(body.host)
    database_cfg["port"] = int(body.port)
    database_cfg["dbname"] = _normalize_setup_text(body.dbname)
    database_cfg["user"] = _normalize_setup_text(body.user)
    database_cfg["password"] = body.password or ""
    database_cfg["minconn"] = max(1, int(database_cfg.get("minconn") or settings.db_minconn))
    database_cfg["maxconn"] = max(database_cfg["minconn"], int(database_cfg.get("maxconn") or settings.db_maxconn))

    next_raw["app"] = app_cfg
    next_raw["security"] = security_cfg
    next_raw["database"] = database_cfg
    return next_raw


@router.get("/status", response_model=StatusResponse)
def get_status(_: object = Depends(require_permission("system:status:read"))) -> StatusResponse:
    db_status = "up" if db_pool.ping() else "down"
    return StatusResponse(
        service="up",
        db=db_status,
        ingestion="unknown",
        time=datetime.now(app_timezone()),
    )


@router.get("/setup/status", response_model=SetupStatusResponse)
def get_setup_status() -> SetupStatusResponse:
    raw = read_raw_config()
    config_path = config_file_path()
    summary = _db_summary_from_raw(raw)
    database_cfg = _as_dict(raw.get("database"))
    app_cfg = _as_dict(raw.get("app"))
    if not summary.host and not config_path.exists():
        summary = SetupDatabaseSummary(
            host=str(settings.db_host or "").strip(),
            port=max(1, int(settings.db_port or 5432)),
            dbname=str(settings.db_name or "").strip(),
            user=str(settings.db_user or "").strip(),
            has_password=bool(str(settings.db_password or "")),
        )
    force_setup = bool(app_cfg.get("force_setup", False))
    setup_completed = bool(app_cfg.get("setup_completed", False)) and not force_setup
    db_configured = bool(summary.host and summary.dbname and summary.user)
    db_test = (
        _test_database_connection(
            host=summary.host,
            port=summary.port,
            dbname=summary.dbname,
            user=summary.user,
            password=str(database_cfg.get("password") or settings.db_password or ""),
        )
        if db_configured
        else None
    )
    db_reachable = bool(db_test and db_test.ok)
    schema_ready = False
    admin_exists = False
    detail = None if db_test is None else db_test.detail
    if db_reachable:
        schema_ready, admin_exists, inspect_detail = _inspect_db_setup_state(summary)
        if inspect_detail:
            detail = inspect_detail
    if not force_setup and not setup_completed and db_configured and db_reachable and schema_ready and admin_exists:
        setup_completed = True
        settings.setup_completed = True
    next_step, default_detail = _next_setup_step(
        db_configured=db_configured,
        db_reachable=db_reachable,
        schema_ready=schema_ready,
        admin_exists=admin_exists,
        setup_completed=setup_completed,
    )
    if force_setup and db_configured and db_reachable and schema_ready and admin_exists:
        default_detail = "已进入重新配置模式，可以沿用当前数据库配置重新初始化。"
    return SetupStatusResponse(
        setup_required=not setup_completed,
        setup_completed=setup_completed,
        config_exists=config_path.exists(),
        db_configured=db_configured,
        db_reachable=db_reachable,
        schema_ready=schema_ready,
        admin_exists=admin_exists,
        config_path=str(config_path.resolve()),
        database=summary,
        next_step=next_step,
        detail=default_detail if not setup_completed else detail,
    )


@router.post("/setup/test-db", response_model=SetupDbTestResponse)
def test_setup_database(body: SetupDbConfigRequest) -> SetupDbTestResponse:
    return _test_database_connection(
        host=_normalize_setup_text(body.host),
        port=int(body.port),
        dbname=_normalize_setup_text(body.dbname),
        user=_normalize_setup_text(body.user),
        password=body.password or "",
    )


@router.post("/setup/initialize", response_model=SetupInitializeResponse)
def initialize_setup(body: SetupInitializeRequest) -> SetupInitializeResponse:
    username = _normalize_setup_text(body.admin_username).lower()
    current_raw = read_raw_config()
    force_setup = bool(_as_dict(current_raw.get("app")).get("force_setup", False))
    if settings.setup_completed and not force_setup:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="setup already completed")
    if not username:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="admin username is required")
    if len(body.admin_password or "") < 8:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="admin password must be at least 8 characters")
    db_test = _test_database_connection(
        host=_normalize_setup_text(body.host),
        port=int(body.port),
        dbname=_normalize_setup_text(body.dbname),
        user=_normalize_setup_text(body.user),
        password=body.password or "",
    )
    if not db_test.ok:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=db_test.detail)

    pending_raw = _build_setup_config(current_raw, body, setup_completed=False, force_setup=force_setup)
    config_path = write_raw_config(pending_raw)
    apply_runtime_config(pending_raw)
    db_pool.reset()

    try:
        migration_result = run_db_migrations()
        user_auth_service.ensure_schema()
        user_auth_service.upsert_user(username, body.admin_password, role="admin", is_active=True)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"setup initialize failed: {exc}") from exc

    completed_raw = _build_setup_config(read_raw_config(), body, setup_completed=True, force_setup=False)
    config_path = write_raw_config(completed_raw)
    apply_runtime_config(completed_raw)
    db_pool.reset()

    return SetupInitializeResponse(
        status="initialized",
        config_path=str(config_path.resolve()),
        setup_completed=True,
        admin_username=username,
        applied_migrations=migration_result.applied,
        skipped_migrations=migration_result.skipped,
        detail="database initialized and first admin created",
    )
