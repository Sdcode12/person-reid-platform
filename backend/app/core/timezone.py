from __future__ import annotations

from datetime import datetime, timezone, tzinfo
from functools import lru_cache
from zoneinfo import ZoneInfo
from zoneinfo import ZoneInfoNotFoundError

from app.core.settings import settings


@lru_cache(maxsize=1)
def app_timezone() -> tzinfo:
    name = str(settings.app_timezone or "Asia/Shanghai").strip() or "Asia/Shanghai"
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return timezone.utc


def ensure_aware(dt: datetime, fallback_tz: tzinfo | None = None) -> datetime:
    if dt.tzinfo is not None:
        return dt
    tz = fallback_tz or app_timezone()
    return dt.replace(tzinfo=tz)


def parse_iso_datetime(raw: object, fallback_tz: tzinfo | None = None) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return ensure_aware(dt, fallback_tz=fallback_tz)

