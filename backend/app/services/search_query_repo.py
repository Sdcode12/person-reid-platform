from __future__ import annotations

import json
from typing import Any

from psycopg2.extras import Json

from app.db.pool import db_pool

_INSERT_SEARCH_QUERY_SQL = """
INSERT INTO search_queries (
    query_id,
    created_by,
    upper_color,
    lower_color,
    time_start,
    time_end,
    camera_id,
    image_mode,
    has_hat,
    pose_hint,
    min_quality_score,
    face_mode,
    group_by_target,
    diverse_camera,
    top_k,
    result_count,
    elapsed_ms,
    funnel,
    metrics
)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (query_id) DO UPDATE
SET
    created_by = EXCLUDED.created_by,
    upper_color = EXCLUDED.upper_color,
    lower_color = EXCLUDED.lower_color,
    time_start = EXCLUDED.time_start,
    time_end = EXCLUDED.time_end,
    camera_id = EXCLUDED.camera_id,
    image_mode = EXCLUDED.image_mode,
    has_hat = EXCLUDED.has_hat,
    pose_hint = EXCLUDED.pose_hint,
    min_quality_score = EXCLUDED.min_quality_score,
    face_mode = EXCLUDED.face_mode,
    group_by_target = EXCLUDED.group_by_target,
    diverse_camera = EXCLUDED.diverse_camera,
    top_k = EXCLUDED.top_k,
    result_count = EXCLUDED.result_count,
    elapsed_ms = EXCLUDED.elapsed_ms,
    funnel = EXCLUDED.funnel,
    metrics = EXCLUDED.metrics
"""

_LIST_SEARCH_QUERIES_SQL = """
SELECT
    q.query_id,
    q.created_by,
    q.created_at,
    q.upper_color,
    q.lower_color,
    q.time_start,
    q.time_end,
    q.camera_id,
    q.image_mode,
    q.has_hat,
    q.pose_hint,
    q.min_quality_score,
    q.face_mode,
    q.group_by_target,
    q.diverse_camera,
    q.top_k,
    q.result_count,
    q.elapsed_ms,
    q.funnel,
    q.metrics,
    COALESCE(COUNT(*) FILTER (WHERE f.verdict = 'hit'), 0) AS hit_count,
    COALESCE(COUNT(*) FILTER (WHERE f.verdict = 'miss'), 0) AS miss_count,
    MAX(f.created_at) AS latest_feedback_at
FROM search_queries q
LEFT JOIN search_feedback f ON f.query_id = q.query_id
WHERE (%s IS NULL OR q.created_by = %s)
GROUP BY
    q.query_id,
    q.created_by,
    q.created_at,
    q.upper_color,
    q.lower_color,
    q.time_start,
    q.time_end,
    q.camera_id,
    q.image_mode,
    q.has_hat,
    q.pose_hint,
    q.min_quality_score,
    q.face_mode,
    q.group_by_target,
    q.diverse_camera,
    q.top_k,
    q.result_count,
    q.elapsed_ms,
    q.funnel,
    q.metrics
ORDER BY q.created_at DESC
LIMIT %s
"""


def _normalize_text(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _normalize_dict(value: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return value


def _read_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            decoded = json.loads(value)
        except Exception:
            return {}
        if isinstance(decoded, dict):
            return decoded
    return {}


class SearchQueryRepo:
    def insert_query(
        self,
        *,
        query_id: str,
        created_by: str,
        upper_color: str | None,
        lower_color: str | None,
        time_start: Any,
        time_end: Any,
        camera_id: str | None,
        image_mode: str | None,
        has_hat: bool | None,
        pose_hint: str | None,
        min_quality_score: float | None,
        face_mode: str,
        group_by_target: bool,
        diverse_camera: bool,
        top_k: int,
        result_count: int,
        elapsed_ms: int,
        funnel: dict[str, Any] | None,
        metrics: dict[str, Any] | None,
    ) -> None:
        pool = None
        conn = None
        try:
            pool = db_pool.get_pool()
            conn = pool.getconn()
            with conn.cursor() as cur:
                cur.execute(
                    _INSERT_SEARCH_QUERY_SQL,
                    (
                        query_id.strip(),
                        created_by.strip(),
                        _normalize_text(upper_color),
                        _normalize_text(lower_color),
                        time_start,
                        time_end,
                        _normalize_text(camera_id),
                        _normalize_text(image_mode),
                        has_hat,
                        _normalize_text(pose_hint),
                        min_quality_score,
                        face_mode.strip().lower() or "assist",
                        bool(group_by_target),
                        bool(diverse_camera),
                        max(1, int(top_k)),
                        max(0, int(result_count)),
                        max(0, int(elapsed_ms)),
                        Json(_normalize_dict(funnel)),
                        Json(_normalize_dict(metrics)),
                    ),
                )
            conn.commit()
        except Exception:
            if conn is not None:
                conn.rollback()
            raise
        finally:
            if pool is not None and conn is not None:
                pool.putconn(conn)

    def list_queries(
        self,
        *,
        limit: int,
        created_by: str | None,
    ) -> list[dict[str, Any]]:
        pool = None
        conn = None
        try:
            pool = db_pool.get_pool()
            conn = pool.getconn()
            with conn.cursor() as cur:
                cur.execute(
                    _LIST_SEARCH_QUERIES_SQL,
                    (
                        _normalize_text(created_by),
                        _normalize_text(created_by),
                        max(1, int(limit)),
                    ),
                )
                rows = cur.fetchall()
        finally:
            if pool is not None and conn is not None:
                pool.putconn(conn)

        items: list[dict[str, Any]] = []
        for row in rows:
            items.append(
                {
                    "query_id": row[0],
                    "created_by": row[1],
                    "created_at": row[2],
                    "upper_color": row[3],
                    "lower_color": row[4],
                    "time_start": row[5],
                    "time_end": row[6],
                    "camera_id": row[7],
                    "image_mode": row[8],
                    "has_hat": row[9],
                    "pose_hint": row[10],
                    "min_quality_score": float(row[11]) if row[11] is not None else None,
                    "face_mode": row[12] or "assist",
                    "group_by_target": bool(row[13]),
                    "diverse_camera": bool(row[14]),
                    "top_k": int(row[15] or 0),
                    "result_count": int(row[16] or 0),
                    "elapsed_ms": int(row[17] or 0),
                    "funnel": _read_dict(row[18]),
                    "metrics": _read_dict(row[19]),
                    "hit_count": int(row[20] or 0),
                    "miss_count": int(row[21] or 0),
                    "latest_feedback_at": row[22],
                }
            )
        return items


search_query_repo = SearchQueryRepo()
