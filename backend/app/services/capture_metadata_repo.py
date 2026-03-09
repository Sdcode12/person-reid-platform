from __future__ import annotations

import mimetypes
from datetime import datetime
from pathlib import Path
from typing import Any

from psycopg2.extras import Json

from app.core.constants import Color
from app.core.image_mode import IMAGE_MODE_COLOR, IMAGE_MODE_LOW_LIGHT_COLOR, normalize_image_mode
from app.core.timezone import parse_iso_datetime
from app.db.pool import db_pool
from app.services.capture_control_service import capture_control_service

_VECTOR_DIM = 512
_POSE_ALLOWED = {"front_or_back", "side", "partial_or_close"}
_CREATE_VECTOR_EXTENSION_SQL = "CREATE EXTENSION IF NOT EXISTS vector;"
_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS capture_metadata (
    meta_id BIGSERIAL PRIMARY KEY,
    image_path TEXT NOT NULL UNIQUE,
    camera_id VARCHAR(64) NOT NULL,
    captured_at TIMESTAMPTZ NOT NULL,
    upper_color VARCHAR(20) NOT NULL DEFAULT 'unknown',
    upper_color_conf REAL,
    lower_color VARCHAR(20) NOT NULL DEFAULT 'unknown',
    lower_color_conf REAL,
    head_color VARCHAR(20) NOT NULL DEFAULT 'unknown',
    has_hat BOOLEAN,
    image_mode VARCHAR(24) NOT NULL DEFAULT 'unknown',
    is_night BOOLEAN,
    pose_hint VARCHAR(32),
    target_key TEXT,
    quality_score REAL,
    people_count INTEGER,
    person_confidence REAL,
    person_area_ratio REAL,
    body_vec VECTOR(512) NOT NULL,
    upper_embedding JSONB NOT NULL DEFAULT '[]'::jsonb,
    lower_embedding JSONB NOT NULL DEFAULT '[]'::jsonb,
    face_embedding JSONB NOT NULL DEFAULT '[]'::jsonb,
    face_confidence REAL,
    image_bytes BYTEA,
    image_mime_type TEXT,
    image_size INTEGER,
    raw JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_capture_metadata_time ON capture_metadata(captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_capture_metadata_camera_time ON capture_metadata(camera_id, captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_capture_metadata_color_time ON capture_metadata(upper_color, lower_color, captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_capture_metadata_quality ON capture_metadata(quality_score DESC);
CREATE INDEX IF NOT EXISTS idx_capture_metadata_target_key_time ON capture_metadata(target_key, captured_at DESC);
"""
_UPSERT_SQL = """
INSERT INTO capture_metadata (
    image_path,
    camera_id,
    captured_at,
    upper_color,
    upper_color_conf,
    lower_color,
    lower_color_conf,
    head_color,
    has_hat,
    image_mode,
    is_night,
    pose_hint,
    target_key,
    quality_score,
    people_count,
    person_confidence,
    person_area_ratio,
    body_vec,
    upper_embedding,
    lower_embedding,
    face_embedding,
    face_confidence,
    image_bytes,
    image_mime_type,
    image_size,
    raw,
    updated_at
)
VALUES (
    %s, -- image_path
    %s, -- camera_id
    %s, -- captured_at
    %s, -- upper_color
    %s, -- upper_color_conf
    %s, -- lower_color
    %s, -- lower_color_conf
    %s, -- head_color
    %s, -- has_hat
    %s, -- image_mode
    %s, -- is_night
    %s, -- pose_hint
    %s, -- target_key
    %s, -- quality_score
    %s, -- people_count
    %s, -- person_confidence
    %s, -- person_area_ratio
    %s::vector, -- body_vec
    %s, -- upper_embedding
    %s, -- lower_embedding
    %s, -- face_embedding
    %s, -- face_confidence
    %s, -- image_bytes
    %s, -- image_mime_type
    %s, -- image_size
    %s, -- raw
    NOW()
)
ON CONFLICT (image_path) DO UPDATE
SET
    target_key = COALESCE(capture_metadata.target_key, EXCLUDED.target_key),
    upper_color_conf = COALESCE(EXCLUDED.upper_color_conf, capture_metadata.upper_color_conf),
    lower_color_conf = COALESCE(EXCLUDED.lower_color_conf, capture_metadata.lower_color_conf),
    upper_embedding = CASE
        WHEN capture_metadata.upper_embedding = '[]'::jsonb THEN EXCLUDED.upper_embedding
        ELSE capture_metadata.upper_embedding
    END,
    lower_embedding = CASE
        WHEN capture_metadata.lower_embedding = '[]'::jsonb THEN EXCLUDED.lower_embedding
        ELSE capture_metadata.lower_embedding
    END,
    face_embedding = CASE
        WHEN capture_metadata.face_embedding = '[]'::jsonb THEN EXCLUDED.face_embedding
        ELSE capture_metadata.face_embedding
    END,
    face_confidence = COALESCE(EXCLUDED.face_confidence, capture_metadata.face_confidence),
    image_bytes = COALESCE(capture_metadata.image_bytes, EXCLUDED.image_bytes),
    image_mime_type = COALESCE(capture_metadata.image_mime_type, EXCLUDED.image_mime_type),
    image_size = COALESCE(capture_metadata.image_size, EXCLUDED.image_size),
    raw = EXCLUDED.raw,
    updated_at = NOW()
RETURNING (xmax = 0) AS inserted_flag
"""


def _parse_iso_dt(raw: object) -> datetime | None:
    return parse_iso_datetime(raw)


def _safe_color(raw: object) -> str:
    if not isinstance(raw, str):
        return Color.UNKNOWN
    key = raw.strip().lower()
    if key in Color.ALL:
        return key
    return Color.UNKNOWN


def _safe_pose(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    key = raw.strip().lower()
    if key in _POSE_ALLOWED:
        return key
    return None


def _to_bool(raw: object) -> bool | None:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        key = raw.strip().lower()
        if key in {"1", "true", "yes", "y"}:
            return True
        if key in {"0", "false", "no", "n"}:
            return False
    return None


def _to_int(raw: object) -> int | None:
    try:
        return int(raw)  # type: ignore[arg-type]
    except Exception:
        return None


def _to_float(raw: object) -> float | None:
    try:
        value = float(raw)  # type: ignore[arg-type]
    except Exception:
        return None
    if value != value:
        return None
    return value


def _safe_image_mode(raw: object, fallback_is_night: object = None) -> str:
    mode = normalize_image_mode(raw)
    if mode:
        return mode
    is_night = _to_bool(fallback_is_night)
    if is_night is None:
        return "unknown"
    return IMAGE_MODE_LOW_LIGHT_COLOR if is_night else IMAGE_MODE_COLOR


def _vector_literal(raw_embedding: object) -> str:
    values: list[float] = []
    if isinstance(raw_embedding, list):
        for item in raw_embedding:
            try:
                values.append(float(item))
            except Exception:
                continue
    values = values[:_VECTOR_DIM]
    if len(values) < _VECTOR_DIM:
        values.extend([0.0] * (_VECTOR_DIM - len(values)))
    text = ",".join(f"{v:.6f}" for v in values)
    return f"[{text}]"


def _float_list(raw: object) -> list[float]:
    if not isinstance(raw, list):
        return []
    out: list[float] = []
    for item in raw:
        value = _to_float(item)
        if value is None:
            continue
        out.append(round(float(value), 6))
    return out


def _read_image_payload(image_path: str) -> tuple[bytes | None, str | None, int | None]:
    if not image_path.strip():
        return None, None, None
    path = Path(image_path).expanduser()
    try:
        resolved = path.resolve()
    except Exception:
        return None, None, None
    if not resolved.exists() or not resolved.is_file():
        return None, None, None
    try:
        data = resolved.read_bytes()
    except Exception:
        return None, None, None
    guessed, _ = mimetypes.guess_type(str(resolved))
    media_type = guessed or "application/octet-stream"
    return data, media_type, len(data)


def _normalized_int_list(values: list[object] | None) -> list[int]:
    result: list[int] = []
    seen: set[int] = set()
    for raw in values or []:
        try:
            value = int(raw)
        except Exception:
            continue
        if value <= 0 or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _normalized_text_list(values: list[object] | None) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in values or []:
        text = str(raw or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _build_capture_metadata_where_clause(
    *,
    track_ids: list[int] | None = None,
    image_paths: list[str] | None = None,
    camera_id: str | None,
    upper_color: str | None,
    lower_color: str | None,
    has_hat: bool | None,
    image_mode: str | None,
    is_night: bool | None,
    pose_hint: str | None,
    min_quality_score: float | None,
    time_start: datetime | None,
    time_end: datetime | None,
) -> tuple[list[str], list[Any]]:
    where: list[str] = []
    params: list[Any] = []

    normalized_track_ids = _normalized_int_list(track_ids)
    if normalized_track_ids:
        where.append("meta_id = ANY(%s)")
        params.append(normalized_track_ids)

    normalized_image_paths = _normalized_text_list(image_paths)
    if normalized_image_paths:
        where.append("image_path = ANY(%s)")
        params.append(normalized_image_paths)

    if camera_id:
        where.append("LOWER(camera_id) = LOWER(%s)")
        params.append(camera_id.strip())
    if upper_color:
        where.append("upper_color = %s")
        params.append(_safe_color(upper_color))
    if lower_color:
        where.append("lower_color = %s")
        params.append(_safe_color(lower_color))
    if has_hat is not None:
        where.append("has_hat = %s")
        params.append(bool(has_hat))
    if image_mode:
        where.append("image_mode = %s")
        params.append(_safe_image_mode(image_mode))
    if is_night is not None:
        where.append("is_night = %s")
        params.append(bool(is_night))
    if pose_hint:
        pose = _safe_pose(pose_hint)
        if pose:
            where.append("pose_hint = %s")
            params.append(pose)
    if min_quality_score is not None:
        where.append("quality_score >= %s")
        params.append(float(min_quality_score))
    if time_start is not None:
        where.append("captured_at >= %s")
        params.append(time_start)
    if time_end is not None:
        where.append("captured_at <= %s")
        params.append(time_end)

    return where, params


class CaptureMetadataRepo:
    def _ensure_columns(self, cur: Any) -> None:
        cur.execute("ALTER TABLE capture_metadata ADD COLUMN IF NOT EXISTS target_key TEXT")
        cur.execute("ALTER TABLE capture_metadata ADD COLUMN IF NOT EXISTS image_bytes BYTEA")
        cur.execute("ALTER TABLE capture_metadata ADD COLUMN IF NOT EXISTS image_mime_type TEXT")
        cur.execute("ALTER TABLE capture_metadata ADD COLUMN IF NOT EXISTS image_size INTEGER")
        cur.execute("ALTER TABLE capture_metadata ADD COLUMN IF NOT EXISTS image_mode VARCHAR(24) NOT NULL DEFAULT 'unknown'")
        cur.execute("ALTER TABLE capture_metadata ADD COLUMN IF NOT EXISTS upper_color_conf REAL")
        cur.execute("ALTER TABLE capture_metadata ADD COLUMN IF NOT EXISTS lower_color_conf REAL")
        cur.execute("ALTER TABLE capture_metadata ADD COLUMN IF NOT EXISTS upper_embedding JSONB NOT NULL DEFAULT '[]'::jsonb")
        cur.execute("ALTER TABLE capture_metadata ADD COLUMN IF NOT EXISTS lower_embedding JSONB NOT NULL DEFAULT '[]'::jsonb")
        cur.execute("ALTER TABLE capture_metadata ADD COLUMN IF NOT EXISTS face_embedding JSONB NOT NULL DEFAULT '[]'::jsonb")
        cur.execute("ALTER TABLE capture_metadata ADD COLUMN IF NOT EXISTS face_confidence REAL")
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_capture_metadata_target_key_time ON capture_metadata(target_key, captured_at DESC)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_capture_metadata_image_mode_time ON capture_metadata(image_mode, captured_at DESC)"
        )

    def _safe_unlink_within_output(self, output_dir: Path | None, raw_path: object) -> bool:
        if output_dir is None:
            return False
        text = str(raw_path or "").strip()
        if not text:
            return False
        candidate = Path(text).expanduser()
        if not candidate.is_absolute():
            candidate = output_dir / candidate
        try:
            resolved = candidate.resolve()
        except Exception:
            return False
        if output_dir not in resolved.parents and resolved != output_dir:
            return False
        if not resolved.exists() or not resolved.is_file():
            return False
        try:
            resolved.unlink()
            return True
        except Exception:
            return False

    def _output_dirs_for_cleanup(self) -> list[Path]:
        repo_root = Path(__file__).resolve().parents[3]
        candidates = [
            repo_root / "hikvision_local_capture" / "photos",
            repo_root / "backend" / "data" / "capture_runtime_configs" / "photos",
        ]
        try:
            candidates.append(capture_control_service.get_output_dir().resolve())
        except Exception:
            pass
        unique: list[Path] = []
        seen: set[str] = set()
        for candidate in candidates:
            text = str(candidate)
            if text in seen:
                continue
            seen.add(text)
            unique.append(candidate)
        return unique

    def _safe_unlink_within_outputs(self, output_dirs: list[Path], raw_path: object) -> bool:
        text = str(raw_path or "").strip()
        if not text or text.startswith("db://"):
            return False
        if not output_dirs:
            return False
        raw_candidate = Path(text).expanduser()
        candidates: list[Path]
        if raw_candidate.is_absolute():
            candidates = [raw_candidate]
        else:
            candidates = [output_dir / raw_candidate for output_dir in output_dirs]
        for candidate in candidates:
            try:
                resolved = candidate.resolve()
            except Exception:
                continue
            if not any(output_dir == resolved or output_dir in resolved.parents for output_dir in output_dirs):
                continue
            if not resolved.exists() or not resolved.is_file():
                continue
            try:
                resolved.unlink()
                return True
            except Exception:
                continue
        return False

    def _ensure_schema(self, cur: Any) -> None:
        cur.execute("SAVEPOINT ensure_vector_ext")
        try:
            cur.execute(_CREATE_VECTOR_EXTENSION_SQL)
        except Exception:
            cur.execute("ROLLBACK TO SAVEPOINT ensure_vector_ext")
        finally:
            cur.execute("RELEASE SAVEPOINT ensure_vector_ext")
        cur.execute(_CREATE_TABLE_SQL)
        self._ensure_columns(cur)

    def _build_upsert_payload(
        self,
        *,
        item: dict[str, Any],
        image_bytes: bytes | None,
        image_mime_type: str | None,
        image_size: int | None,
    ) -> tuple[Any, ...] | None:
        image_path = str(item.get("image_path", "")).strip()
        if not image_path:
            return None
        captured_at = _parse_iso_dt(item.get("captured_at")) or _parse_iso_dt(item.get("event_time"))
        if captured_at is None:
            return None
        camera_id = str(item.get("camera_id", "")).strip() or str(item.get("camera_host", "")).strip() or "unknown"
        mime = (image_mime_type or "").split(";", 1)[0].strip().lower() or None
        size: int | None = image_size
        if size is None and image_bytes is not None:
            size = len(image_bytes)
        return (
            image_path,
            camera_id,
            captured_at,
            _safe_color(item.get("upper_color")),
            _to_float(item.get("upper_color_conf")),
            _safe_color(item.get("lower_color")),
            _to_float(item.get("lower_color_conf")),
            _safe_color(item.get("head_color")),
            _to_bool(item.get("has_hat")),
            _safe_image_mode(item.get("image_mode"), item.get("is_night")),
            _to_bool(item.get("is_night")),
            _safe_pose(item.get("pose_hint")),
            str(item.get("target_key", "")).strip() or None,
            _to_float(item.get("quality_score")),
            _to_int(item.get("people_count")),
            _to_float(item.get("person_confidence")),
            _to_float(item.get("person_area_ratio")),
            _vector_literal(item.get("body_embedding")),
            Json(_float_list(item.get("upper_embedding"))),
            Json(_float_list(item.get("lower_embedding"))),
            Json(_float_list(item.get("face_embedding"))),
            _to_float(item.get("face_confidence")),
            image_bytes,
            mime,
            size,
            Json(item),
        )

    def _upsert_one(self, cur: Any, payload: tuple[Any, ...]) -> bool:
        cur.execute(_UPSERT_SQL, payload)
        row = cur.fetchone()
        return bool(row and row[0])

    def upsert_item(
        self,
        *,
        item: dict[str, Any],
        image_bytes: bytes | None = None,
        image_mime_type: str | None = None,
        image_size: int | None = None,
    ) -> dict[str, int]:
        payload = self._build_upsert_payload(
            item=item,
            image_bytes=image_bytes,
            image_mime_type=image_mime_type,
            image_size=image_size,
        )
        if payload is None:
            return {"inserted": 0, "updated": 0, "skipped": 1, "errors": 0}

        pool = None
        conn = None
        try:
            pool = db_pool.get_pool()
            conn = pool.getconn()
            with conn.cursor() as cur:
                self._ensure_schema(cur)
                inserted = self._upsert_one(cur, payload)
            conn.commit()
            if inserted:
                return {"inserted": 1, "updated": 0, "skipped": 0, "errors": 0}
            return {"inserted": 0, "updated": 1, "skipped": 0, "errors": 0}
        except Exception:
            if conn is not None:
                conn.rollback()
            raise
        finally:
            if pool is not None and conn is not None:
                pool.putconn(conn)

    def sync_from_local(self, scan_limit: int = 5000, purge_local_images: bool = False) -> dict[str, int]:
        scan_limit = max(1, min(scan_limit, 200000))
        items = capture_control_service.read_metadata_items(limit=scan_limit)
        if not items:
            return {
                "scanned": 0,
                "inserted": 0,
                "updated": 0,
                "skipped": 0,
                "errors": 0,
                "purged_local_images": 0,
                "purged_local_sidecars": 0,
            }

        pool = None
        conn = None
        inserted = 0
        updated = 0
        skipped = 0
        errors = 0
        purged_local_images = 0
        purged_local_sidecars = 0
        output_dir: Path | None = None
        if purge_local_images:
            try:
                output_dir = capture_control_service.get_output_dir().resolve()
            except Exception:
                output_dir = None
        try:
            pool = db_pool.get_pool()
            conn = pool.getconn()
            with conn.cursor() as cur:
                self._ensure_schema(cur)
                for idx, item in enumerate(reversed(items), start=1):
                    if not isinstance(item, dict):
                        skipped += 1
                        continue
                    image_path = str(item.get("image_path", "")).strip()
                    if not image_path:
                        skipped += 1
                        continue
                    image_bytes, image_mime_type, image_size = _read_image_payload(image_path)
                    payload = self._build_upsert_payload(
                        item=item,
                        image_bytes=image_bytes,
                        image_mime_type=image_mime_type,
                        image_size=image_size,
                    )
                    if payload is None:
                        skipped += 1
                        continue
                    savepoint = f"capture_metadata_row_{idx}"
                    cur.execute(f"SAVEPOINT {savepoint}")
                    try:
                        inserted_flag = self._upsert_one(cur, payload)
                    except Exception:
                        cur.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                        errors += 1
                    else:
                        if inserted_flag:
                            inserted += 1
                        else:
                            updated += 1
                        if purge_local_images and image_bytes is not None:
                            if self._safe_unlink_within_output(output_dir, image_path):
                                purged_local_images += 1
                            if self._safe_unlink_within_output(output_dir, item.get("sidecar_path")):
                                purged_local_sidecars += 1
                    finally:
                        cur.execute(f"RELEASE SAVEPOINT {savepoint}")
            conn.commit()
        except Exception:
            if conn is not None:
                conn.rollback()
            raise
        finally:
            if pool is not None and conn is not None:
                pool.putconn(conn)

        return {
            "scanned": len(items),
            "inserted": inserted,
            "updated": updated,
            "skipped": skipped,
            "errors": errors,
            "purged_local_images": purged_local_images,
            "purged_local_sidecars": purged_local_sidecars,
        }

    def count_records(self) -> int:
        pool = None
        conn = None
        try:
            pool = db_pool.get_pool()
            conn = pool.getconn()
            with conn.cursor() as cur:
                self._ensure_schema(cur)
                cur.execute("SELECT COUNT(*) FROM capture_metadata")
                row = cur.fetchone()
                total = int(row[0] if row else 0)
            conn.commit()
            return total
        except Exception:
            if conn is not None:
                conn.rollback()
            raise
        finally:
            if pool is not None and conn is not None:
                pool.putconn(conn)

    def query_items(
        self,
        *,
        limit: int,
        track_ids: list[int] | None = None,
        image_paths: list[str] | None = None,
        camera_id: str | None,
        upper_color: str | None,
        lower_color: str | None,
        has_hat: bool | None,
        image_mode: str | None,
        is_night: bool | None,
        pose_hint: str | None,
        min_quality_score: float | None,
        time_start: datetime | None,
        time_end: datetime | None,
    ) -> list[dict[str, Any]]:
        where, params = _build_capture_metadata_where_clause(
            track_ids=track_ids,
            image_paths=image_paths,
            camera_id=camera_id,
            upper_color=upper_color,
            lower_color=lower_color,
            has_hat=has_hat,
            image_mode=image_mode,
            is_night=is_night,
            pose_hint=pose_hint,
            min_quality_score=min_quality_score,
            time_start=time_start,
            time_end=time_end,
        )

        sql = """
            SELECT
                meta_id,
                camera_id,
                captured_at,
                upper_color,
                lower_color,
                has_hat,
                image_mode,
                is_night,
                pose_hint,
                quality_score,
                upper_color_conf,
                lower_color_conf,
                people_count,
                target_key,
                image_path
            FROM capture_metadata
        """
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY captured_at DESC LIMIT %s"
        params.append(max(1, min(limit, 10000)))

        pool = None
        conn = None
        try:
            pool = db_pool.get_pool()
            conn = pool.getconn()
            with conn.cursor() as cur:
                self._ensure_schema(cur)
                cur.execute(sql, params)
                rows = cur.fetchall()
            conn.commit()
        except Exception:
            if conn is not None:
                conn.rollback()
            raise
        finally:
            if pool is not None and conn is not None:
                pool.putconn(conn)

        result: list[dict[str, Any]] = []
        for row in rows:
            result.append(
                {
                    "track_id": int(row[0]),
                    "camera_id": str(row[1]),
                    "captured_at": row[2],
                    "upper_color": str(row[3]),
                    "lower_color": str(row[4]),
                    "has_hat": row[5],
                    "image_mode": str(row[6] or "unknown"),
                    "is_night": row[7],
                    "pose_hint": row[8],
                    "quality_score": row[9],
                    "upper_color_conf": row[10],
                    "lower_color_conf": row[11],
                    "people_count": row[12],
                    "target_key": str(row[13] or "").strip() or None,
                    "image_path": str(row[14]),
                }
            )
        return result

    def count_items(
        self,
        *,
        track_ids: list[int] | None = None,
        image_paths: list[str] | None = None,
        camera_id: str | None,
        upper_color: str | None,
        lower_color: str | None,
        has_hat: bool | None,
        image_mode: str | None,
        is_night: bool | None,
        pose_hint: str | None,
        min_quality_score: float | None,
        time_start: datetime | None,
        time_end: datetime | None,
    ) -> int:
        where, params = _build_capture_metadata_where_clause(
            track_ids=track_ids,
            image_paths=image_paths,
            camera_id=camera_id,
            upper_color=upper_color,
            lower_color=lower_color,
            has_hat=has_hat,
            image_mode=image_mode,
            is_night=is_night,
            pose_hint=pose_hint,
            min_quality_score=min_quality_score,
            time_start=time_start,
            time_end=time_end,
        )
        sql = "SELECT COUNT(*) FROM capture_metadata"
        if where:
            sql += " WHERE " + " AND ".join(where)

        pool = None
        conn = None
        try:
            pool = db_pool.get_pool()
            conn = pool.getconn()
            with conn.cursor() as cur:
                self._ensure_schema(cur)
                cur.execute(sql, params)
                row = cur.fetchone()
            conn.commit()
            return int(row[0] if row else 0)
        except Exception:
            if conn is not None:
                conn.rollback()
            raise
        finally:
            if pool is not None and conn is not None:
                pool.putconn(conn)

    def delete_items(
        self,
        *,
        track_ids: list[int] | None = None,
        image_paths: list[str] | None = None,
        camera_id: str | None,
        upper_color: str | None,
        lower_color: str | None,
        has_hat: bool | None,
        image_mode: str | None,
        is_night: bool | None,
        pose_hint: str | None,
        min_quality_score: float | None,
        time_start: datetime | None,
        time_end: datetime | None,
        delete_local_files: bool = True,
        dry_run: bool = False,
    ) -> dict[str, int]:
        where, params = _build_capture_metadata_where_clause(
            track_ids=track_ids,
            image_paths=image_paths,
            camera_id=camera_id,
            upper_color=upper_color,
            lower_color=lower_color,
            has_hat=has_hat,
            image_mode=image_mode,
            is_night=is_night,
            pose_hint=pose_hint,
            min_quality_score=min_quality_score,
            time_start=time_start,
            time_end=time_end,
        )
        if not where:
            raise ValueError("at least one selection or filter is required for delete")

        where_sql = " WHERE " + " AND ".join(where)
        pool = None
        conn = None
        matched = 0
        deleted_paths: list[tuple[str, str]] = []
        try:
            pool = db_pool.get_pool()
            conn = pool.getconn()
            with conn.cursor() as cur:
                self._ensure_schema(cur)
                cur.execute(f"SELECT COUNT(*) FROM capture_metadata{where_sql}", params)
                row = cur.fetchone()
                matched = int(row[0] if row else 0)
                if not dry_run and matched > 0:
                    cur.execute(
                        f"""
                        DELETE FROM capture_metadata
                        {where_sql}
                        RETURNING image_path, COALESCE(raw->>'sidecar_path', '')
                        """,
                        params,
                    )
                    rows = cur.fetchall()
                    deleted_paths = [(str(path or ""), str(sidecar or "")) for path, sidecar in rows]
            conn.commit()
        except Exception:
            if conn is not None:
                conn.rollback()
            raise
        finally:
            if pool is not None and conn is not None:
                pool.putconn(conn)

        deleted_local_images = 0
        deleted_local_sidecars = 0
        if delete_local_files and not dry_run and deleted_paths:
            output_dirs = self._output_dirs_for_cleanup()
            for image_path, sidecar_path in deleted_paths:
                if self._safe_unlink_within_outputs(output_dirs, image_path):
                    deleted_local_images += 1
                if self._safe_unlink_within_outputs(output_dirs, sidecar_path):
                    deleted_local_sidecars += 1

        return {
            "matched": matched,
            "deleted": 0 if dry_run else len(deleted_paths),
            "deleted_local_images": deleted_local_images,
            "deleted_local_sidecars": deleted_local_sidecars,
        }

    def list_search_feature_backfill_candidates(
        self,
        *,
        limit: int,
        after_meta_id: int = 0,
        only_missing: bool = True,
    ) -> list[dict[str, Any]]:
        sql = """
            SELECT
                meta_id,
                image_path,
                image_bytes,
                person_area_ratio,
                raw,
                quality_score,
                upper_color,
                lower_color,
                upper_color_conf,
                lower_color_conf,
                upper_embedding,
                lower_embedding,
                face_embedding,
                face_confidence
            FROM capture_metadata
            WHERE meta_id > %s
        """
        params: list[Any] = [max(0, int(after_meta_id))]
        if only_missing:
            sql += """
                AND (
                    upper_color_conf IS NULL
                    OR lower_color_conf IS NULL
                    OR upper_embedding = '[]'::jsonb
                    OR lower_embedding = '[]'::jsonb
                    OR face_confidence IS NULL
                )
            """
        sql += " ORDER BY meta_id ASC LIMIT %s"
        params.append(max(1, min(limit, 2000)))

        pool = None
        conn = None
        try:
            pool = db_pool.get_pool()
            conn = pool.getconn()
            with conn.cursor() as cur:
                self._ensure_schema(cur)
                cur.execute(sql, params)
                rows = cur.fetchall()
            conn.commit()
        except Exception:
            if conn is not None:
                conn.rollback()
            raise
        finally:
            if pool is not None and conn is not None:
                pool.putconn(conn)

        result: list[dict[str, Any]] = []
        for row in rows:
            result.append(
                {
                    "meta_id": int(row[0]),
                    "track_id": int(row[0]),
                    "image_path": str(row[1] or "").strip(),
                    "image_bytes": row[2],
                    "person_area_ratio": _to_float(row[3]),
                    "raw": row[4] if isinstance(row[4], dict) else {},
                    "quality_score": _to_float(row[5]),
                    "upper_color": str(row[6] or "").strip() or Color.UNKNOWN,
                    "lower_color": str(row[7] or "").strip() or Color.UNKNOWN,
                    "upper_color_conf": _to_float(row[8]),
                    "lower_color_conf": _to_float(row[9]),
                    "upper_embedding": row[10] if isinstance(row[10], list) else [],
                    "lower_embedding": row[11] if isinstance(row[11], list) else [],
                    "face_embedding": row[12] if isinstance(row[12], list) else [],
                    "face_confidence": _to_float(row[13]),
                }
            )
        return result

    def update_search_features(
        self,
        *,
        meta_id: int,
        upper_color: str | None,
        lower_color: str | None,
        upper_color_conf: float | None,
        lower_color_conf: float | None,
        upper_embedding: list[float],
        lower_embedding: list[float],
        face_embedding: list[float],
        face_confidence: float | None,
        quality_score: float | None,
        person_area_ratio: float | None,
        image_mode: str | None,
    ) -> bool:
        pool = None
        conn = None
        try:
            pool = db_pool.get_pool()
            conn = pool.getconn()
            with conn.cursor() as cur:
                self._ensure_schema(cur)
                cur.execute(
                    """
                    UPDATE capture_metadata
                    SET
                        upper_color = COALESCE(%s, upper_color),
                        lower_color = COALESCE(%s, lower_color),
                        upper_color_conf = COALESCE(%s, upper_color_conf),
                        lower_color_conf = COALESCE(%s, lower_color_conf),
                        upper_embedding = %s,
                        lower_embedding = %s,
                        face_embedding = %s,
                        face_confidence = COALESCE(%s, face_confidence),
                        quality_score = COALESCE(%s, quality_score),
                        person_area_ratio = COALESCE(%s, person_area_ratio),
                        image_mode = COALESCE(%s, image_mode),
                        updated_at = NOW()
                    WHERE meta_id = %s
                    """,
                    (
                        _safe_color(upper_color),
                        _safe_color(lower_color),
                        upper_color_conf,
                        lower_color_conf,
                        Json(_float_list(upper_embedding)),
                        Json(_float_list(lower_embedding)),
                        Json(_float_list(face_embedding)),
                        face_confidence,
                        quality_score,
                        person_area_ratio,
                        _safe_image_mode(image_mode),
                        int(meta_id),
                    ),
                )
                updated = cur.rowcount > 0
            conn.commit()
            return bool(updated)
        except Exception:
            if conn is not None:
                conn.rollback()
            raise
        finally:
            if pool is not None and conn is not None:
                pool.putconn(conn)

    def get_photo(self, image_path: str) -> tuple[bytes, str] | None:
        path = image_path.strip()
        if not path:
            return None
        pool = None
        conn = None
        try:
            pool = db_pool.get_pool()
            conn = pool.getconn()
            with conn.cursor() as cur:
                self._ensure_schema(cur)
                cur.execute(
                    """
                    SELECT image_bytes, image_mime_type
                    FROM capture_metadata
                    WHERE image_path = %s
                    LIMIT 1
                    """,
                    (path,),
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

        if not row:
            return None
        image_bytes = row[0]
        if image_bytes is None:
            return None
        media_type = str(row[1] or "application/octet-stream")
        return bytes(image_bytes), media_type

    def get_photo_by_track_id(self, track_id: int) -> tuple[bytes, str] | None:
        if track_id <= 0:
            return None
        pool = None
        conn = None
        try:
            pool = db_pool.get_pool()
            conn = pool.getconn()
            with conn.cursor() as cur:
                self._ensure_schema(cur)
                cur.execute(
                    """
                    SELECT image_bytes, image_mime_type
                    FROM capture_metadata
                    WHERE meta_id = %s
                    LIMIT 1
                    """,
                    (int(track_id),),
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

        if not row:
            return None
        image_bytes = row[0]
        if image_bytes is None:
            return None
        media_type = str(row[1] or "application/octet-stream")
        return bytes(image_bytes), media_type

    def load_candidate_assets(self, track_ids: list[int]) -> dict[int, dict[str, Any]]:
        ids = sorted({int(track_id) for track_id in track_ids if int(track_id) > 0})
        if not ids:
            return {}

        placeholders = ", ".join(["%s"] * len(ids))
        sql = f"""
            SELECT
                meta_id,
                image_path,
                image_bytes,
                person_area_ratio,
                raw,
                upper_embedding,
                lower_embedding,
                face_embedding,
                face_confidence,
                quality_score
            FROM capture_metadata
            WHERE meta_id IN ({placeholders})
        """

        pool = None
        conn = None
        try:
            pool = db_pool.get_pool()
            conn = pool.getconn()
            with conn.cursor() as cur:
                self._ensure_schema(cur)
                cur.execute(sql, ids)
                rows = cur.fetchall()
            conn.commit()
        except Exception:
            if conn is not None:
                conn.rollback()
            raise
        finally:
            if pool is not None and conn is not None:
                pool.putconn(conn)

        result: dict[int, dict[str, Any]] = {}
        for row in rows:
            track_id = int(row[0])
            result[track_id] = {
                "track_id": track_id,
                "image_path": str(row[1] or "").strip(),
                "image_bytes": row[2],
                "person_area_ratio": _to_float(row[3]),
                "raw": row[4] if isinstance(row[4], dict) else {},
                "upper_embedding": row[5] if isinstance(row[5], list) else [],
                "lower_embedding": row[6] if isinstance(row[6], list) else [],
                "face_embedding": row[7] if isinstance(row[7], list) else [],
                "face_confidence": _to_float(row[8]),
                "quality_score": _to_float(row[9]),
            }
        return result

    def _build_search_filters(
        self,
        *,
        camera_id: str | None,
        has_hat: bool | None,
        image_mode: str | None,
        is_night: bool | None,
        pose_hint: str | None,
        min_quality_score: float | None,
        time_start: datetime | None,
        time_end: datetime | None,
    ) -> tuple[list[str], list[Any]]:
        where: list[str] = []
        params: list[Any] = []
        if camera_id:
            where.append("LOWER(camera_id) = LOWER(%s)")
            params.append(camera_id.strip())
        if has_hat is not None:
            where.append("has_hat = %s")
            params.append(bool(has_hat))
        if image_mode:
            where.append("image_mode = %s")
            params.append(_safe_image_mode(image_mode))
        if is_night is not None:
            where.append("is_night = %s")
            params.append(bool(is_night))
        if pose_hint:
            pose = _safe_pose(pose_hint)
            if pose:
                where.append("pose_hint = %s")
                params.append(pose)
        if min_quality_score is not None:
            where.append("quality_score >= %s")
            params.append(float(min_quality_score))
        if time_start is not None:
            where.append("captured_at >= %s")
            params.append(time_start)
        if time_end is not None:
            where.append("captured_at <= %s")
            params.append(time_end)
        return where, params

    @staticmethod
    def _search_select_sql(*, include_vector_score: bool) -> str:
        if include_vector_score:
            return """
                SELECT
                    meta_id,
                    camera_id,
                    captured_at,
                    upper_color,
                    lower_color,
                    has_hat,
                    image_mode,
                    is_night,
                    pose_hint,
                    quality_score,
                    upper_color_conf,
                    lower_color_conf,
                    target_key,
                    image_path,
                    raw,
                    GREATEST(0.0, 1 - (body_vec <=> %s::vector)) AS body_sim
                FROM capture_metadata
            """
        return """
            SELECT
                meta_id,
                camera_id,
                captured_at,
                upper_color,
                lower_color,
                has_hat,
                image_mode,
                is_night,
                pose_hint,
                quality_score,
                upper_color_conf,
                lower_color_conf,
                target_key,
                image_path,
                raw,
                0.5::real AS body_sim
            FROM capture_metadata
        """

    @staticmethod
    def _rows_to_search_candidates(rows: list[Any]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for row in rows:
            result.append(
                {
                    "track_id": int(row[0]),
                    "camera_id": str(row[1]),
                    "captured_at": row[2],
                    "upper_color": str(row[3]),
                    "lower_color": str(row[4]),
                    "has_hat": row[5],
                    "image_mode": str(row[6] or "unknown"),
                    "is_night": row[7],
                    "pose_hint": row[8],
                    "quality_score": row[9],
                    "upper_color_conf": row[10],
                    "lower_color_conf": row[11],
                    "target_key": str(row[12] or "").strip() or None,
                    "image_path": str(row[13]),
                    "raw": row[14] if isinstance(row[14], dict) else {},
                    "body_sim": float(row[15] if row[15] is not None else 0.0),
                }
            )
        return result

    def count_search_candidates(
        self,
        *,
        camera_id: str | None,
        has_hat: bool | None,
        image_mode: str | None,
        is_night: bool | None,
        pose_hint: str | None,
        min_quality_score: float | None,
        time_start: datetime | None,
        time_end: datetime | None,
    ) -> int:
        where, params = self._build_search_filters(
            camera_id=camera_id,
            has_hat=has_hat,
            image_mode=image_mode,
            is_night=is_night,
            pose_hint=pose_hint,
            min_quality_score=min_quality_score,
            time_start=time_start,
            time_end=time_end,
        )
        sql = "SELECT COUNT(*) FROM capture_metadata"
        if where:
            sql += " WHERE " + " AND ".join(where)

        pool = None
        conn = None
        try:
            pool = db_pool.get_pool()
            conn = pool.getconn()
            with conn.cursor() as cur:
                self._ensure_schema(cur)
                cur.execute(sql, params)
                row = cur.fetchone()
            conn.commit()
        except Exception:
            if conn is not None:
                conn.rollback()
            raise
        finally:
            if pool is not None and conn is not None:
                pool.putconn(conn)
        return int(row[0] or 0) if row else 0

    def search_candidates_exact(
        self,
        *,
        query_vector: str | None,
        limit: int,
        camera_id: str | None,
        has_hat: bool | None,
        image_mode: str | None,
        is_night: bool | None,
        pose_hint: str | None,
        min_quality_score: float | None,
        time_start: datetime | None,
        time_end: datetime | None,
    ) -> list[dict[str, Any]]:
        where, params = self._build_search_filters(
            camera_id=camera_id,
            has_hat=has_hat,
            image_mode=image_mode,
            is_night=is_night,
            pose_hint=pose_hint,
            min_quality_score=min_quality_score,
            time_start=time_start,
            time_end=time_end,
        )
        sql = self._search_select_sql(include_vector_score=bool(query_vector))
        if query_vector:
            params = [query_vector] + params
        if where:
            sql += " WHERE " + " AND ".join(where)
        if query_vector:
            sql += " ORDER BY body_vec <=> %s::vector ASC, captured_at DESC LIMIT %s"
            params.extend([query_vector, max(1, min(limit, 5000))])
        else:
            sql += " ORDER BY captured_at DESC LIMIT %s"
            params.append(max(1, min(limit, 5000)))

        pool = None
        conn = None
        try:
            pool = db_pool.get_pool()
            conn = pool.getconn()
            with conn.cursor() as cur:
                self._ensure_schema(cur)
                cur.execute(sql, params)
                rows = cur.fetchall()
            conn.commit()
        except Exception:
            if conn is not None:
                conn.rollback()
            raise
        finally:
            if pool is not None and conn is not None:
                pool.putconn(conn)
        return self._rows_to_search_candidates(rows)

    def search_candidates_ann(
        self,
        *,
        query_vector: str,
        limit: int,
        camera_id: str | None,
        has_hat: bool | None,
        image_mode: str | None,
        is_night: bool | None,
        pose_hint: str | None,
        min_quality_score: float | None,
        time_start: datetime | None,
        time_end: datetime | None,
        ef_search: int = 100,
    ) -> list[dict[str, Any]]:
        where, params = self._build_search_filters(
            camera_id=camera_id,
            has_hat=has_hat,
            image_mode=image_mode,
            is_night=is_night,
            pose_hint=pose_hint,
            min_quality_score=min_quality_score,
            time_start=time_start,
            time_end=time_end,
        )
        sql = self._search_select_sql(include_vector_score=True)
        params = [query_vector] + params
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY body_vec <=> %s::vector ASC, captured_at DESC LIMIT %s"
        params.extend([query_vector, max(1, min(limit, 5000))])

        pool = None
        conn = None
        try:
            pool = db_pool.get_pool()
            conn = pool.getconn()
            with conn.cursor() as cur:
                self._ensure_schema(cur)
                cur.execute("SET LOCAL hnsw.ef_search = %s", (max(20, int(ef_search)),))
                cur.execute("SAVEPOINT search_ann_settings")
                try:
                    cur.execute("SET LOCAL hnsw.iterative_scan = 'strict_order'")
                except Exception:
                    cur.execute("ROLLBACK TO SAVEPOINT search_ann_settings")
                finally:
                    cur.execute("RELEASE SAVEPOINT search_ann_settings")
                cur.execute(sql, params)
                rows = cur.fetchall()
            conn.commit()
        except Exception:
            if conn is not None:
                conn.rollback()
            raise
        finally:
            if pool is not None and conn is not None:
                pool.putconn(conn)
        return self._rows_to_search_candidates(rows)

    def search_candidates(
        self,
        *,
        query_vector: str | None,
        limit: int,
        camera_id: str | None,
        has_hat: bool | None,
        image_mode: str | None,
        is_night: bool | None,
        pose_hint: str | None,
        min_quality_score: float | None,
        time_start: datetime | None,
        time_end: datetime | None,
        strategy: str = "exact",
        ef_search: int = 100,
    ) -> list[dict[str, Any]]:
        selected = (strategy or "exact").strip().lower()
        if selected == "ann" and query_vector:
            return self.search_candidates_ann(
                query_vector=query_vector,
                limit=limit,
                camera_id=camera_id,
                has_hat=has_hat,
                image_mode=image_mode,
                is_night=is_night,
                pose_hint=pose_hint,
                min_quality_score=min_quality_score,
                time_start=time_start,
                time_end=time_end,
                ef_search=ef_search,
            )
        return self.search_candidates_exact(
            query_vector=query_vector,
            limit=limit,
            camera_id=camera_id,
            has_hat=has_hat,
            image_mode=image_mode,
            is_night=is_night,
            pose_hint=pose_hint,
            min_quality_score=min_quality_score,
            time_start=time_start,
            time_end=time_end,
        )


capture_metadata_repo = CaptureMetadataRepo()
