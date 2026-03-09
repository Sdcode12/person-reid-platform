from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.timezone import app_timezone, ensure_aware, parse_iso_datetime
from app.db.pool import db_pool
from app.models.schemas import (
    AnalyticsDashboardResponse,
    AnalyticsDistributionItem,
    AnalyticsTopCameraItem,
    AnalyticsTrendPoint,
)
from app.services.capture_control_service import capture_control_service
from app.services.capture_metadata_repo import capture_metadata_repo

_GRANULARITY_ALLOWED = {"auto", "hour", "day", "week"}
_MODE_LABELS = {
    "color": "彩色",
    "low_light_color": "低照度彩色",
    "ir_bw": "红外黑白",
    "unknown": "未知",
}


@dataclass
class _BucketPoint:
    label: str
    bucket_start: datetime
    bucket_end: datetime
    value: int


def _ensure_dt(value: datetime | None, fallback: datetime) -> datetime:
    if value is None:
        return fallback
    return ensure_aware(value)


def _normalize_range(
    range_start: datetime | None,
    range_end: datetime | None,
) -> tuple[datetime, datetime]:
    now = datetime.now(timezone.utc)
    end = _ensure_dt(range_end, now)
    start = _ensure_dt(range_start, end - timedelta(days=7))
    if start > end:
        raise ValueError("range_start must be <= range_end")
    return start, end


def _resolve_granularity(granularity: str, range_start: datetime, range_end: datetime) -> str:
    key = (granularity or "auto").strip().lower()
    if key not in _GRANULARITY_ALLOWED:
        raise ValueError("invalid granularity")
    if key != "auto":
        return key
    span = max(0.0, (range_end - range_start).total_seconds())
    if span <= 48 * 3600:
        return "hour"
    if span <= 60 * 24 * 3600:
        return "day"
    return "week"


def _bucket_floor(value: datetime, granularity: str) -> datetime:
    local = ensure_aware(value).astimezone(app_timezone())
    if granularity == "hour":
        return local.replace(minute=0, second=0, microsecond=0)
    if granularity == "day":
        return local.replace(hour=0, minute=0, second=0, microsecond=0)
    weekday = local.weekday()
    day_start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return day_start - timedelta(days=weekday)


def _bucket_step(granularity: str) -> timedelta:
    if granularity == "hour":
        return timedelta(hours=1)
    if granularity == "day":
        return timedelta(days=1)
    return timedelta(days=7)


def _bucket_label(value: datetime, granularity: str) -> str:
    local = ensure_aware(value).astimezone(app_timezone())
    if granularity == "hour":
        return local.strftime("%m-%d %H:00")
    if granularity == "day":
        return local.strftime("%m-%d")
    return local.strftime("%m-%d")


def _today_start(now: datetime | None = None) -> datetime:
    base = ensure_aware(now or datetime.now(timezone.utc)).astimezone(app_timezone())
    return base.replace(hour=0, minute=0, second=0, microsecond=0)


def _safe_ratio(part: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round(part / float(total), 6)


def _distribution_items(counter: Counter[str], total: int, label_map: dict[str, str] | None = None) -> list[AnalyticsDistributionItem]:
    items: list[AnalyticsDistributionItem] = []
    for key, value in counter.most_common():
        label = label_map.get(key, key) if label_map else key
        items.append(
            AnalyticsDistributionItem(
                key=key,
                label=label,
                value=int(value),
                ratio=_safe_ratio(int(value), total),
            )
        )
    return items


def _top_camera_items(counter: Counter[str], total: int) -> list[AnalyticsTopCameraItem]:
    items: list[AnalyticsTopCameraItem] = []
    for camera_id, value in counter.most_common(8):
        label = camera_id or "unknown"
        items.append(
            AnalyticsTopCameraItem(
                camera_id=camera_id or "unknown",
                label=label,
                value=int(value),
                ratio=_safe_ratio(int(value), total),
            )
        )
    return items


def _empty_response(
    *,
    source: str,
    range_start: datetime,
    range_end: datetime,
    granularity: str,
    camera_id: str | None,
    note: str | None = None,
) -> AnalyticsDashboardResponse:
    return AnalyticsDashboardResponse(
        generated_at=datetime.now(timezone.utc),
        source=source,
        range_start=range_start,
        range_end=range_end,
        granularity=granularity,
        camera_id=camera_id,
        total_count=0,
        today_count=0,
        range_count=0,
        previous_range_count=0,
        range_change_ratio=None,
        active_camera_count=0,
        trend=[],
        camera_distribution=[],
        mode_distribution=[],
        top_cameras=[],
        note=note,
    )


class AnalyticsService:
    def _build_where(self, *, camera_id: str | None = None, range_start: datetime | None = None, range_end: datetime | None = None) -> tuple[str, list[Any]]:
        where: list[str] = []
        params: list[Any] = []
        if camera_id:
            where.append("LOWER(camera_id) = LOWER(%s)")
            params.append(camera_id.strip())
        if range_start is not None:
            where.append("captured_at >= %s")
            params.append(range_start)
        if range_end is not None:
            where.append("captured_at <= %s")
            params.append(range_end)
        if not where:
            return "", params
        return " WHERE " + " AND ".join(where), params

    def _query_db(
        self,
        *,
        range_start: datetime,
        range_end: datetime,
        granularity: str,
        camera_id: str | None,
    ) -> AnalyticsDashboardResponse:
        pool = None
        conn = None
        today_start = _today_start().astimezone(timezone.utc)
        previous_range_end = range_start
        previous_range_start = previous_range_end - (range_end - range_start)
        tz_name = str(app_timezone())

        try:
            pool = db_pool.get_pool()
            conn = pool.getconn()
            with conn.cursor() as cur:
                capture_metadata_repo._ensure_schema(cur)

                where_total, params_total = self._build_where(camera_id=camera_id)
                cur.execute(f"SELECT COUNT(*) FROM capture_metadata{where_total}", params_total)
                total_count = int((cur.fetchone() or [0])[0] or 0)

                where_today, params_today = self._build_where(camera_id=camera_id, range_start=today_start, range_end=range_end)
                cur.execute(f"SELECT COUNT(*) FROM capture_metadata{where_today}", params_today)
                today_count = int((cur.fetchone() or [0])[0] or 0)

                where_range, params_range = self._build_where(camera_id=camera_id, range_start=range_start, range_end=range_end)
                cur.execute(f"SELECT COUNT(*) FROM capture_metadata{where_range}", params_range)
                range_count = int((cur.fetchone() or [0])[0] or 0)

                where_prev, params_prev = self._build_where(
                    camera_id=camera_id,
                    range_start=previous_range_start,
                    range_end=previous_range_end,
                )
                cur.execute(f"SELECT COUNT(*) FROM capture_metadata{where_prev}", params_prev)
                previous_range_count = int((cur.fetchone() or [0])[0] or 0)

                cur.execute(f"SELECT COUNT(DISTINCT camera_id) FROM capture_metadata{where_range}", params_range)
                active_camera_count = int((cur.fetchone() or [0])[0] or 0)

                trend_sql = f"""
                    SELECT
                        date_trunc('{granularity}', captured_at AT TIME ZONE %s) AS bucket_local,
                        COUNT(*)
                    FROM capture_metadata
                    {where_range}
                    GROUP BY 1
                    ORDER BY 1 ASC
                """
                cur.execute(trend_sql, [tz_name, *params_range])
                trend_rows = cur.fetchall()

                cur.execute(
                    f"""
                    SELECT COALESCE(camera_id, 'unknown') AS key, COUNT(*) AS cnt
                    FROM capture_metadata
                    {where_range}
                    GROUP BY 1
                    ORDER BY cnt DESC, key ASC
                    LIMIT 6
                    """,
                    params_range,
                )
                camera_rows = cur.fetchall()

                cur.execute(
                    f"""
                    SELECT COALESCE(NULLIF(image_mode, ''), 'unknown') AS key, COUNT(*) AS cnt
                    FROM capture_metadata
                    {where_range}
                    GROUP BY 1
                    ORDER BY cnt DESC, key ASC
                    LIMIT 6
                    """,
                    params_range,
                )
                mode_rows = cur.fetchall()

                cur.execute(
                    f"""
                    SELECT COALESCE(camera_id, 'unknown') AS key, COUNT(*) AS cnt
                    FROM capture_metadata
                    {where_range}
                    GROUP BY 1
                    ORDER BY cnt DESC, key ASC
                    LIMIT 8
                    """,
                    params_range,
                )
                top_rows = cur.fetchall()

            conn.commit()
        except Exception:
            if conn is not None:
                conn.rollback()
            raise
        finally:
            if pool is not None and conn is not None:
                pool.putconn(conn)

        step = _bucket_step(granularity)
        bucket_start = _bucket_floor(range_start, granularity)
        bucket_end = _bucket_floor(range_end, granularity)
        trend_map: dict[datetime, int] = {}
        for row in trend_rows:
            local_dt = ensure_aware(row[0], app_timezone())
            trend_map[local_dt] = int(row[1] or 0)

        points: list[AnalyticsTrendPoint] = []
        cursor = bucket_start
        while cursor <= bucket_end:
            next_cursor = cursor + step
            points.append(
                AnalyticsTrendPoint(
                    label=_bucket_label(cursor, granularity),
                    bucket_start=cursor,
                    bucket_end=next_cursor,
                    value=trend_map.get(cursor, 0),
                )
            )
            cursor = next_cursor

        camera_counter = Counter({str(row[0]): int(row[1] or 0) for row in camera_rows})
        mode_counter = Counter({str(row[0]): int(row[1] or 0) for row in mode_rows})
        top_counter = Counter({str(row[0]): int(row[1] or 0) for row in top_rows})
        change_ratio = None
        if previous_range_count > 0:
            change_ratio = round((range_count - previous_range_count) / float(previous_range_count), 6)

        return AnalyticsDashboardResponse(
            generated_at=datetime.now(timezone.utc),
            source="db",
            range_start=range_start,
            range_end=range_end,
            granularity=granularity,
            camera_id=camera_id,
            total_count=total_count,
            today_count=today_count,
            range_count=range_count,
            previous_range_count=previous_range_count,
            range_change_ratio=change_ratio,
            active_camera_count=active_camera_count,
            trend=points,
            camera_distribution=_distribution_items(camera_counter, range_count),
            mode_distribution=_distribution_items(mode_counter, range_count, _MODE_LABELS),
            top_cameras=_top_camera_items(top_counter, range_count),
            note="数据来自 PostgreSQL 聚合统计",
        )

    def _item_dt(self, item: dict[str, Any]) -> datetime | None:
        return parse_iso_datetime(item.get("captured_at")) or parse_iso_datetime(item.get("event_time"))

    def _local_items(self, limit: int = 50000) -> list[dict[str, Any]]:
        try:
            items = capture_control_service.read_metadata_items(limit=limit)
        except Exception:
            return []
        return [item for item in items if isinstance(item, dict)]

    def _query_local(
        self,
        *,
        range_start: datetime,
        range_end: datetime,
        granularity: str,
        camera_id: str | None,
    ) -> AnalyticsDashboardResponse:
        try:
            total_count = capture_control_service.count_metadata_items()
        except Exception:
            total_count = 0
        items = self._local_items()
        if camera_id:
            normalized = camera_id.strip().lower()
            items = [item for item in items if str(item.get("camera_id", "")).strip().lower() == normalized]

        if not items and total_count <= 0:
            return _empty_response(
                source="local",
                range_start=range_start,
                range_end=range_end,
                granularity=granularity,
                camera_id=camera_id,
                note="当前没有可统计的抓拍数据",
            )

        previous_range_end = range_start
        previous_range_start = previous_range_end - (range_end - range_start)
        today_start = _today_start()

        range_counter: Counter[datetime] = Counter()
        camera_counter: Counter[str] = Counter()
        mode_counter: Counter[str] = Counter()
        top_counter: Counter[str] = Counter()
        today_count = 0
        range_count = 0
        previous_range_count = 0
        active_cameras: set[str] = set()

        for item in items:
            captured_at = self._item_dt(item)
            if captured_at is None:
                continue
            local_dt = ensure_aware(captured_at).astimezone(app_timezone())
            if local_dt >= today_start and local_dt <= range_end.astimezone(app_timezone()):
                today_count += 1
            if previous_range_start <= captured_at <= previous_range_end:
                previous_range_count += 1
            if not (range_start <= captured_at <= range_end):
                continue
            range_count += 1
            bucket = _bucket_floor(captured_at, granularity)
            range_counter[bucket] += 1
            camera_key = str(item.get("camera_id", "")).strip() or "unknown"
            mode_key = str(item.get("image_mode", "")).strip() or "unknown"
            camera_counter[camera_key] += 1
            mode_counter[mode_key] += 1
            top_counter[camera_key] += 1
            active_cameras.add(camera_key)

        step = _bucket_step(granularity)
        bucket_start = _bucket_floor(range_start, granularity)
        bucket_end = _bucket_floor(range_end, granularity)
        points: list[AnalyticsTrendPoint] = []
        cursor = bucket_start
        while cursor <= bucket_end:
            next_cursor = cursor + step
            points.append(
                AnalyticsTrendPoint(
                    label=_bucket_label(cursor, granularity),
                    bucket_start=cursor,
                    bucket_end=next_cursor,
                    value=int(range_counter.get(cursor, 0)),
                )
            )
            cursor = next_cursor

        change_ratio = None
        if previous_range_count > 0:
            change_ratio = round((range_count - previous_range_count) / float(previous_range_count), 6)

        return AnalyticsDashboardResponse(
            generated_at=datetime.now(timezone.utc),
            source="local",
            range_start=range_start,
            range_end=range_end,
            granularity=granularity,
            camera_id=camera_id,
            total_count=total_count if not camera_id else len(items),
            today_count=today_count,
            range_count=range_count,
            previous_range_count=previous_range_count,
            range_change_ratio=change_ratio,
            active_camera_count=len(active_cameras),
            trend=points,
            camera_distribution=_distribution_items(camera_counter, range_count),
            mode_distribution=_distribution_items(mode_counter, range_count, _MODE_LABELS),
            top_cameras=_top_camera_items(top_counter, range_count),
            note="数据库不可用，当前展示本地 metadata.jsonl 聚合结果",
        )

    def build_dashboard(
        self,
        *,
        range_start: datetime | None = None,
        range_end: datetime | None = None,
        granularity: str = "auto",
        camera_id: str | None = None,
    ) -> AnalyticsDashboardResponse:
        start, end = _normalize_range(range_start, range_end)
        resolved_granularity = _resolve_granularity(granularity, start, end)
        camera = (camera_id or "").strip() or None
        try:
            return self._query_db(
                range_start=start,
                range_end=end,
                granularity=resolved_granularity,
                camera_id=camera,
            )
        except Exception:
            return self._query_local(
                range_start=start,
                range_end=end,
                granularity=resolved_granularity,
                camera_id=camera,
            )


analytics_service = AnalyticsService()
