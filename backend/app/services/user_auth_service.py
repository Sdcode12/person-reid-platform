from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.core.rbac import VALID_ROLES
from app.core.security import hash_password_pbkdf2, verify_password
from app.db.pool import db_pool

MAX_FAILED_LOGIN_ATTEMPTS = 5
ACCOUNT_LOCK_MINUTES = 15


@dataclass
class DBAuthResult:
    status: str
    role: str | None = None
    user: dict[str, object] | None = None
    locked_until: datetime | None = None


class UserAuthService:
    USER_COLUMNS = """
        user_id,
        username,
        role,
        is_active,
        created_at,
        last_login_at,
        password_updated_at,
        failed_login_count,
        locked_until,
        must_change_password
    """

    @staticmethod
    def _normalize_username(username: str) -> str:
        return (username or "").strip().lower()

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _as_utc(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def _row_to_user(self, row: tuple[object, ...]) -> dict[str, object]:
        return {
            "user_id": int(row[0]),
            "username": str(row[1] or ""),
            "role": str(row[2] or "").strip().lower(),
            "is_active": bool(row[3]),
            "created_at": row[4],
            "last_login_at": row[5],
            "password_updated_at": row[6],
            "failed_login_count": int(row[7] or 0),
            "locked_until": row[8],
            "must_change_password": bool(row[9]),
        }

    def ensure_schema(self) -> None:
        pool = None
        conn = None
        try:
            pool = db_pool.get_pool()
            conn = pool.getconn()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    ALTER TABLE users
                    ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMPTZ
                    """
                )
                cur.execute(
                    """
                    ALTER TABLE users
                    ADD COLUMN IF NOT EXISTS password_updated_at TIMESTAMPTZ
                    """
                )
                cur.execute(
                    """
                    ALTER TABLE users
                    ADD COLUMN IF NOT EXISTS failed_login_count INTEGER NOT NULL DEFAULT 0
                    """
                )
                cur.execute(
                    """
                    ALTER TABLE users
                    ADD COLUMN IF NOT EXISTS locked_until TIMESTAMPTZ
                    """
                )
                cur.execute(
                    """
                    ALTER TABLE users
                    ADD COLUMN IF NOT EXISTS must_change_password BOOLEAN NOT NULL DEFAULT FALSE
                    """
                )
                cur.execute(
                    """
                    UPDATE users
                    SET password_updated_at = COALESCE(password_updated_at, created_at, NOW())
                    WHERE password_updated_at IS NULL
                    """
                )
                cur.execute(
                    """
                    UPDATE users
                    SET failed_login_count = COALESCE(failed_login_count, 0)
                    WHERE failed_login_count IS NULL
                    """
                )
                cur.execute(
                    """
                    UPDATE users
                    SET must_change_password = COALESCE(must_change_password, FALSE)
                    WHERE must_change_password IS NULL
                    """
                )
            conn.commit()
        except Exception:
            if conn is not None:
                conn.rollback()
            raise
        finally:
            if pool is not None and conn is not None:
                pool.putconn(conn)

    def get_user(self, username: str, *, strict: bool = False) -> dict[str, object] | None:
        user = self._normalize_username(username)
        if not user:
            return None
        pool = None
        conn = None
        try:
            pool = db_pool.get_pool()
            conn = pool.getconn()
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT {self.USER_COLUMNS}
                    FROM users
                    WHERE username = %s
                    LIMIT 1
                    """,
                    (user,),
                )
                row = cur.fetchone()
            conn.commit()
        except Exception:
            if conn is not None:
                conn.rollback()
            if strict:
                raise
            return None
        finally:
            if pool is not None and conn is not None:
                pool.putconn(conn)
        if not row:
            return None
        return self._row_to_user(row)

    def list_users(self) -> list[dict[str, object]]:
        pool = None
        conn = None
        try:
            pool = db_pool.get_pool()
            conn = pool.getconn()
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT {self.USER_COLUMNS}
                    FROM users
                    ORDER BY username ASC
                    """
                )
                rows = cur.fetchall()
            conn.commit()
        except Exception:
            if conn is not None:
                conn.rollback()
            raise
        finally:
            if pool is not None and conn is not None:
                pool.putconn(conn)
        return [self._row_to_user(row) for row in rows]

    def _count_active_admins(self, conn) -> int:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*)
                FROM users
                WHERE role = 'admin' AND is_active = TRUE
                """
            )
            row = cur.fetchone()
        return int(row[0] or 0) if row else 0

    def authenticate_db(self, username: str, password: str) -> DBAuthResult:
        user = self._normalize_username(username)
        if not user:
            return DBAuthResult(status="invalid")
        pool = None
        conn = None
        try:
            pool = db_pool.get_pool()
            conn = pool.getconn()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        user_id,
                        username,
                        password_hash,
                        role,
                        is_active,
                        created_at,
                        last_login_at,
                        password_updated_at,
                        failed_login_count,
                        locked_until,
                        must_change_password
                    FROM users
                    WHERE username = %s
                    LIMIT 1
                    """,
                    (user,),
                )
                row = cur.fetchone()
                if not row:
                    conn.commit()
                    return DBAuthResult(status="not_found")

                role = str(row[3] or "").strip().lower()
                is_active = bool(row[4])
                locked_until = self._as_utc(row[9])
                now = self._now()

                if not is_active or role not in VALID_ROLES:
                    conn.commit()
                    return DBAuthResult(status="invalid")

                if locked_until and locked_until > now:
                    conn.commit()
                    return DBAuthResult(status="locked", role=role, locked_until=locked_until)

                stored_hash = str(row[2] or "")
                if not verify_password(password, stored_hash):
                    next_failed_count = int(row[8] or 0) + 1
                    next_locked_until = None
                    if next_failed_count >= MAX_FAILED_LOGIN_ATTEMPTS:
                        next_locked_until = now + timedelta(minutes=ACCOUNT_LOCK_MINUTES)
                    cur.execute(
                        """
                        UPDATE users
                        SET
                            failed_login_count = %s,
                            locked_until = %s
                        WHERE username = %s
                        """,
                        (next_failed_count, next_locked_until, user),
                    )
                    conn.commit()
                    return DBAuthResult(
                        status="locked" if next_locked_until else "invalid",
                        role=role,
                        locked_until=next_locked_until,
                    )

                cur.execute(
                    f"""
                    UPDATE users
                    SET
                        last_login_at = NOW(),
                        failed_login_count = 0,
                        locked_until = NULL
                    WHERE username = %s
                    RETURNING {self.USER_COLUMNS}
                    """,
                    (user,),
                )
                updated = cur.fetchone()
            conn.commit()
        except Exception:
            if conn is not None:
                conn.rollback()
            return DBAuthResult(status="db_unavailable")
        finally:
            if pool is not None and conn is not None:
                pool.putconn(conn)

        auth_user = self._row_to_user(updated)
        return DBAuthResult(status="ok", role=str(auth_user["role"]), user=auth_user)

    def create_user(self, username: str, password: str, role: str, is_active: bool = True) -> dict[str, object]:
        user = self._normalize_username(username)
        normalized_role = str(role or "").strip().lower()
        if not user:
            raise ValueError("username is required")
        if normalized_role not in VALID_ROLES:
            raise ValueError("invalid role")
        password_hash = hash_password_pbkdf2(password)
        pool = None
        conn = None
        try:
            pool = db_pool.get_pool()
            conn = pool.getconn()
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO users(username, password_hash, role, is_active, password_updated_at, must_change_password)
                    VALUES (%s, %s, %s, %s, NOW(), FALSE)
                    RETURNING {self.USER_COLUMNS}
                    """,
                    (user, password_hash, normalized_role, bool(is_active)),
                )
                row = cur.fetchone()
            conn.commit()
        except Exception as exc:
            if conn is not None:
                conn.rollback()
            message = str(exc).lower()
            if "unique" in message or "duplicate" in message:
                raise ValueError("username already exists") from exc
            raise
        finally:
            if pool is not None and conn is not None:
                pool.putconn(conn)
        return self._row_to_user(row)

    def upsert_user(self, username: str, password: str, role: str, is_active: bool = True) -> dict[str, object]:
        user = self._normalize_username(username)
        normalized_role = str(role or "").strip().lower()
        if not user:
            raise ValueError("username is required")
        if normalized_role not in VALID_ROLES:
            raise ValueError("invalid role")
        password_hash = hash_password_pbkdf2(password)
        pool = None
        conn = None
        try:
            pool = db_pool.get_pool()
            conn = pool.getconn()
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO users(username, password_hash, role, is_active, password_updated_at, must_change_password)
                    VALUES (%s, %s, %s, %s, NOW(), FALSE)
                    ON CONFLICT (username)
                    DO UPDATE SET
                        password_hash = EXCLUDED.password_hash,
                        role = EXCLUDED.role,
                        is_active = EXCLUDED.is_active,
                        password_updated_at = NOW(),
                        must_change_password = FALSE,
                        failed_login_count = 0,
                        locked_until = NULL
                    RETURNING {self.USER_COLUMNS}
                    """,
                    (user, password_hash, normalized_role, bool(is_active)),
                )
                row = cur.fetchone()
            conn.commit()
        except Exception:
            if conn is not None:
                conn.rollback()
            raise
        finally:
            if pool is not None and conn is not None:
                pool.putconn(conn)
        return self._row_to_user(row)

    def update_user(
        self,
        username: str,
        *,
        role: str | None = None,
        is_active: bool | None = None,
        acting_username: str | None = None,
    ) -> dict[str, object]:
        user = self._normalize_username(username)
        if not user:
            raise ValueError("username is required")
        normalized_role = None if role is None else str(role or "").strip().lower()
        if normalized_role is not None and normalized_role not in VALID_ROLES:
            raise ValueError("invalid role")
        if normalized_role is None and is_active is None:
            raise ValueError("no fields to update")
        pool = None
        conn = None
        try:
            pool = db_pool.get_pool()
            conn = pool.getconn()
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT {self.USER_COLUMNS}
                    FROM users
                    WHERE username = %s
                    LIMIT 1
                    """,
                    (user,),
                )
                row = cur.fetchone()
                if not row:
                    raise LookupError("user not found")
                current = self._row_to_user(row)
                current_role = str(current["role"])
                current_active = bool(current["is_active"])
                if user == self._normalize_username(acting_username or "") and is_active is False:
                    raise ValueError("cannot disable current login user")
                will_be_admin = normalized_role == "admin" if normalized_role is not None else current_role == "admin"
                will_be_active = bool(is_active) if is_active is not None else current_active
                if current_role == "admin" and current_active and not (will_be_admin and will_be_active):
                    if self._count_active_admins(conn) <= 1:
                        raise ValueError("at least one active admin must remain")
                cur.execute(
                    f"""
                    UPDATE users
                    SET
                        role = COALESCE(%s, role),
                        is_active = COALESCE(%s, is_active)
                    WHERE username = %s
                    RETURNING {self.USER_COLUMNS}
                    """,
                    (normalized_role, is_active, user),
                )
                updated = cur.fetchone()
            conn.commit()
        except Exception:
            if conn is not None:
                conn.rollback()
            raise
        finally:
            if pool is not None and conn is not None:
                pool.putconn(conn)
        return self._row_to_user(updated)

    def change_password(self, username: str, current_password: str, new_password: str) -> None:
        user = self._normalize_username(username)
        if not user:
            raise ValueError("username is required")
        pool = None
        conn = None
        try:
            pool = db_pool.get_pool()
            conn = pool.getconn()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT password_hash, is_active
                    FROM users
                    WHERE username = %s
                    LIMIT 1
                    """,
                    (user,),
                )
                row = cur.fetchone()
                if not row:
                    raise LookupError("user not found")
                stored_hash = str(row[0] or "")
                if not bool(row[1]):
                    raise ValueError("user is disabled")
                if not verify_password(current_password, stored_hash):
                    raise ValueError("current password is incorrect")
                cur.execute(
                    """
                    UPDATE users
                    SET
                        password_hash = %s,
                        password_updated_at = NOW(),
                        must_change_password = FALSE,
                        failed_login_count = 0,
                        locked_until = NULL
                    WHERE username = %s
                    """,
                    (hash_password_pbkdf2(new_password), user),
                )
            conn.commit()
        except Exception:
            if conn is not None:
                conn.rollback()
            raise
        finally:
            if pool is not None and conn is not None:
                pool.putconn(conn)

    def reset_password(self, username: str, new_password: str) -> None:
        user = self._normalize_username(username)
        if not user:
            raise ValueError("username is required")
        pool = None
        conn = None
        try:
            pool = db_pool.get_pool()
            conn = pool.getconn()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE users
                    SET
                        password_hash = %s,
                        password_updated_at = NOW(),
                        must_change_password = TRUE,
                        failed_login_count = 0,
                        locked_until = NULL
                    WHERE username = %s
                    RETURNING user_id
                    """,
                    (hash_password_pbkdf2(new_password), user),
                )
                row = cur.fetchone()
                if not row:
                    raise LookupError("user not found")
            conn.commit()
        except Exception:
            if conn is not None:
                conn.rollback()
            raise
        finally:
            if pool is not None and conn is not None:
                pool.putconn(conn)


user_auth_service = UserAuthService()
