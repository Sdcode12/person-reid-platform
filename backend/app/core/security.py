from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import hmac

import jwt

from app.core.settings import settings


def create_token(subject: str, role: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject,
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.token_expire_minutes)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict:
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])


def hash_password_pbkdf2(password: str, *, iterations: int = 390000, salt: str | None = None) -> str:
    raw_password = password or ""
    use_salt = salt or hashlib.sha256(str(datetime.now(timezone.utc).timestamp()).encode("utf-8")).hexdigest()[:32]
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        raw_password.encode("utf-8"),
        use_salt.encode("utf-8"),
        int(iterations),
    ).hex()
    return f"pbkdf2_sha256${int(iterations)}${use_salt}${digest}"


def verify_password(password: str, stored_hash: str) -> bool:
    if stored_hash.startswith("pbkdf2_sha256$"):
        parts = stored_hash.split("$", 3)
        if len(parts) != 4:
            return False
        _, raw_iter, salt, expected = parts
        try:
            iterations = int(raw_iter)
        except Exception:
            return False
        actual = hashlib.pbkdf2_hmac(
            "sha256",
            (password or "").encode("utf-8"),
            salt.encode("utf-8"),
            iterations,
        ).hex()
        return hmac.compare_digest(actual, expected)

    if stored_hash.startswith("plain$"):
        return hmac.compare_digest((password or ""), stored_hash[len("plain$") :])

    return hmac.compare_digest((password or ""), stored_hash)
