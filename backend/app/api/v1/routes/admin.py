from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.v1.deps import AuthUser, require_permission
from app.core.rbac import VALID_ROLES, list_permissions
from app.models.schemas import (
    AdminOpsResponse,
    UserCreateRequest,
    UserItem,
    UserListResponse,
    UserPasswordResetRequest,
    UserUpdateRequest,
)
from app.services.monitoring_service import build_admin_overview, build_ops_health
from app.services.user_auth_service import user_auth_service

router = APIRouter(prefix='/admin')


@router.get('/overview')
def overview(_: object = Depends(require_permission('audit:read'))) -> dict[str, int | float | str]:
    try:
        return build_admin_overview()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"failed to load overview: {exc}") from exc


@router.get('/ops', response_model=AdminOpsResponse)
def ops(_: object = Depends(require_permission('system:status:read'))) -> AdminOpsResponse:
    try:
        return AdminOpsResponse(**build_ops_health())
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"failed to load ops health: {exc}") from exc


@router.get('/users', response_model=UserListResponse)
def list_users(_: AuthUser = Depends(require_permission('user:manage'))) -> UserListResponse:
    try:
        items = user_auth_service.list_users()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"failed to load users: {exc}") from exc
    return UserListResponse(
        items=[
            UserItem(
                user_id=int(item.get('user_id', 0)),
                username=str(item.get('username', '')),
                role=str(item.get('role', '')),
                is_active=bool(item.get('is_active', False)),
                created_at=item.get('created_at'),
                last_login_at=item.get('last_login_at'),
                password_updated_at=item.get('password_updated_at'),
                failed_login_count=int(item.get('failed_login_count', 0) or 0),
                locked_until=item.get('locked_until'),
                must_change_password=bool(item.get('must_change_password', False)),
                permissions=list(list_permissions(str(item.get('role', '')).strip().lower())),
            )
            for item in items
        ]
    )


@router.post('/users', response_model=UserItem)
def create_user(
    body: UserCreateRequest,
    _: AuthUser = Depends(require_permission('user:manage')),
) -> UserItem:
    if body.role.strip().lower() not in VALID_ROLES:
        raise HTTPException(status_code=422, detail='invalid role')
    if len(body.password or '') < 8:
        raise HTTPException(status_code=422, detail='password must be at least 8 characters')
    try:
        item = user_auth_service.create_user(body.username, body.password, body.role, is_active=body.is_active)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"failed to create user: {exc}") from exc
    role = str(item.get('role', '')).strip().lower()
    return UserItem(
        user_id=int(item.get('user_id', 0)),
        username=str(item.get('username', '')),
        role=role,
        is_active=bool(item.get('is_active', False)),
        created_at=item.get('created_at'),
        last_login_at=item.get('last_login_at'),
        password_updated_at=item.get('password_updated_at'),
        failed_login_count=int(item.get('failed_login_count', 0) or 0),
        locked_until=item.get('locked_until'),
        must_change_password=bool(item.get('must_change_password', False)),
        permissions=list(list_permissions(role)),
    )


@router.patch('/users/{username}', response_model=UserItem)
def update_user(
    username: str,
    body: UserUpdateRequest,
    user: AuthUser = Depends(require_permission('user:manage')),
) -> UserItem:
    role = None if body.role is None else body.role.strip().lower()
    if role is not None and role not in VALID_ROLES:
        raise HTTPException(status_code=422, detail='invalid role')
    try:
        item = user_auth_service.update_user(
            username,
            role=role,
            is_active=body.is_active,
            acting_username=user.username,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"failed to update user: {exc}") from exc
    final_role = str(item.get('role', '')).strip().lower()
    return UserItem(
        user_id=int(item.get('user_id', 0)),
        username=str(item.get('username', '')),
        role=final_role,
        is_active=bool(item.get('is_active', False)),
        created_at=item.get('created_at'),
        last_login_at=item.get('last_login_at'),
        password_updated_at=item.get('password_updated_at'),
        failed_login_count=int(item.get('failed_login_count', 0) or 0),
        locked_until=item.get('locked_until'),
        must_change_password=bool(item.get('must_change_password', False)),
        permissions=list(list_permissions(final_role)),
    )


@router.put('/users/{username}/password')
def reset_user_password(
    username: str,
    body: UserPasswordResetRequest,
    _: AuthUser = Depends(require_permission('user:manage')),
) -> dict[str, str]:
    if len(body.new_password or '') < 8:
        raise HTTPException(status_code=422, detail='password must be at least 8 characters')
    try:
        user_auth_service.reset_password(username, body.new_password)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"failed to reset user password: {exc}") from exc
    return {"status": "password_reset"}
