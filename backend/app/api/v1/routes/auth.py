from __future__ import annotations

from datetime import timezone

from fastapi import APIRouter, HTTPException, status
from fastapi import Depends

from app.api.v1.deps import AuthUser, require_permission
from app.core.rbac import VALID_ROLES, list_permissions
from app.core.security import create_token
from app.core.settings import settings
from app.models.schemas import (
    AuthMeResponse,
    AuthSecurityInfoResponse,
    ChangePasswordRequest,
    LoginRequest,
    TokenResponse,
)
from app.services.user_auth_service import ACCOUNT_LOCK_MINUTES, MAX_FAILED_LOGIN_ATTEMPTS, user_auth_service

router = APIRouter()
PASSWORD_MIN_LENGTH = 8
AUTH_MODE_ALLOWED = {"db_only"}


def _validate_new_password(password: str) -> None:
    raw = password or ""
    if len(raw) < PASSWORD_MIN_LENGTH:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="password must be at least 8 characters")
    if raw.strip() != raw:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="password must not start or end with spaces")


def _auth_mode() -> str:
    key = str(settings.auth_mode or "db_only").strip().lower()
    if key not in AUTH_MODE_ALLOWED:
        return "db_only"
    return key


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest) -> TokenResponse:
    db_result = user_auth_service.authenticate_db(body.username, body.password)
    if db_result.status == "ok":
        role = str(db_result.role or "").strip().lower()
        username = str((db_result.user or {}).get("username", body.username) or body.username).strip().lower()
    elif db_result.status == "locked":
        locked_until = db_result.locked_until.astimezone(timezone.utc).isoformat() if db_result.locked_until else None
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail=f"account locked until {locked_until}" if locked_until else "account locked",
        )
    elif db_result.status == "db_unavailable":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="database auth unavailable",
        )
    else:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials")

    if role not in VALID_ROLES:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="role misconfigured")

    token = create_token(subject=username, role=role)
    return TokenResponse(access_token=token, token_type="bearer", role=role)


@router.get("/me", response_model=AuthMeResponse)
def me(user: AuthUser = Depends(require_permission("auth:login"))) -> AuthMeResponse:
    return AuthMeResponse(
        user_id=user.user_id,
        username=user.username,
        role=user.role,
        is_active=user.is_active,
        managed_by_db=True,
        created_at=user.created_at,
        last_login_at=user.last_login_at,
        password_updated_at=user.password_updated_at,
        must_change_password=user.must_change_password,
        permissions=list(list_permissions(user.role)),
    )


@router.post("/change-password")
def change_password(
    body: ChangePasswordRequest,
    user: AuthUser = Depends(require_permission("auth:login")),
) -> dict[str, str]:
    _validate_new_password(body.new_password)
    try:
        user_auth_service.change_password(user.username, body.current_password, body.new_password)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="current account not found in database",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"failed to change password: {exc}") from exc
    return {"status": "password_updated"}


@router.get("/security", response_model=AuthSecurityInfoResponse)
def security_info(_: AuthUser = Depends(require_permission("auth:login"))) -> AuthSecurityInfoResponse:
    matrix = {role: list(list_permissions(role)) for role in sorted(VALID_ROLES)}
    return AuthSecurityInfoResponse(
        token_expire_minutes=int(settings.token_expire_minutes),
        jwt_algorithm=str(settings.jwt_algorithm),
        password_min_length=PASSWORD_MIN_LENGTH,
        auth_mode=_auth_mode(),
        max_failed_login_attempts=MAX_FAILED_LOGIN_ATTEMPTS,
        account_lock_minutes=ACCOUNT_LOCK_MINUTES,
        roles=sorted(VALID_ROLES),
        permission_matrix=matrix,
    )
