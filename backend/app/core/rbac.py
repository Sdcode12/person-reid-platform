from __future__ import annotations

from collections.abc import Iterable

VALID_ROLES = {"admin", "operator", "auditor"}

PERMISSION_BY_ROLE: dict[str, set[str]] = {
    "admin": {
        "auth:login",
        "system:status:read",
        "ingestion:status:read",
        "search:run",
        "search:feedback:write",
        "alert:read",
        "alert:ack",
        "audit:read",
        "camera:test",
        "camera:roi:write",
        "camera:config:write",
        "capture:status:read",
        "capture:control",
        "capture:config:write",
        "capture:delete",
        "cleanup:run",
        "user:manage",
        "config:update",
    },
    "operator": {
        "auth:login",
        "system:status:read",
        "ingestion:status:read",
        "search:run",
        "search:feedback:write",
        "alert:read",
        "alert:ack",
        "camera:test",
        "camera:roi:write",
        "camera:config:write",
        "capture:status:read",
        "capture:control",
        "capture:config:write",
    },
    "auditor": {
        "auth:login",
        "system:status:read",
        "ingestion:status:read",
        "search:run",
        "alert:read",
        "audit:read",
        "capture:status:read",
    },
}


def has_permission(role: str, permission: str) -> bool:
    return permission in PERMISSION_BY_ROLE.get(role, set())


def list_permissions(role: str) -> Iterable[str]:
    return sorted(PERMISSION_BY_ROLE.get(role, set()))
