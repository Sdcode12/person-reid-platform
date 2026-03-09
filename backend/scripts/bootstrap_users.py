from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.rbac import VALID_ROLES
from app.db.migrations import run_db_migrations
from app.services.user_auth_service import user_auth_service


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Bootstrap or update a DB-backed login user.")
    parser.add_argument("--username", required=True, help="login username")
    parser.add_argument("--password", required=True, help="plain text password to hash and store")
    parser.add_argument("--role", required=True, choices=sorted(VALID_ROLES), help="role to assign")
    parser.add_argument("--inactive", action="store_true", help="store the account as disabled")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    run_db_migrations()
    user_auth_service.ensure_schema()
    item = user_auth_service.upsert_user(
        username=args.username,
        password=args.password,
        role=args.role,
        is_active=not args.inactive,
    )
    print(
        json.dumps(
            {
                "ok": True,
                "username": item["username"],
                "role": item["role"],
                "is_active": item["is_active"],
                "created_at": item["created_at"].isoformat() if item.get("created_at") else None,
                "last_login_at": item["last_login_at"].isoformat() if item.get("last_login_at") else None,
                "password_updated_at": item["password_updated_at"].isoformat() if item.get("password_updated_at") else None,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
