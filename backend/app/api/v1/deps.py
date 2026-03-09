from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.rbac import has_permission
from app.core.security import decode_token
from app.services.user_auth_service import user_auth_service


@dataclass
class AuthUser:
    user_id: int
    username: str
    role: str
    is_active: bool
    created_at: datetime | None = None
    last_login_at: datetime | None = None
    password_updated_at: datetime | None = None
    failed_login_count: int = 0
    locked_until: datetime | None = None
    must_change_password: bool = False


bearer = HTTPBearer(auto_error=True)


def get_current_user(
    cred: HTTPAuthorizationCredentials = Depends(bearer),
) -> AuthUser:
    try:
        payload = decode_token(cred.credentials)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid token",
        ) from exc

    username = str(payload.get("sub", "") or "").strip().lower()
    issued_at = int(payload.get("iat", 0) or 0)
    if not username or issued_at <= 0:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token")
    try:
        user = user_auth_service.get_user(username, strict=True)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="database auth unavailable",
        ) from exc

    if not user or not bool(user.get("is_active", False)):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="account unavailable")

    db_role = str(user.get("role", "") or "").strip().lower()
    if not db_role:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="account unavailable")

    password_updated_at = user.get("password_updated_at")
    if isinstance(password_updated_at, datetime):
        refreshed_at = password_updated_at.astimezone(timezone.utc) if password_updated_at.tzinfo else password_updated_at.replace(tzinfo=timezone.utc)
        if issued_at < int(refreshed_at.timestamp()):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="token expired")

    return AuthUser(
        user_id=int(user.get("user_id", 0) or 0),
        username=str(user.get("username", username)),
        role=db_role,
        is_active=bool(user.get("is_active", False)),
        created_at=user.get("created_at") if isinstance(user.get("created_at"), datetime) else None,
        last_login_at=user.get("last_login_at") if isinstance(user.get("last_login_at"), datetime) else None,
        password_updated_at=password_updated_at if isinstance(password_updated_at, datetime) else None,
        failed_login_count=int(user.get("failed_login_count", 0) or 0),
        locked_until=user.get("locked_until") if isinstance(user.get("locked_until"), datetime) else None,
        must_change_password=bool(user.get("must_change_password", False)),
    )


def require_permission(permission: str):
    def _checker(user: AuthUser = Depends(get_current_user)) -> AuthUser:
        if not has_permission(user.role, permission):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="permission denied")
        return user

    return _checker
