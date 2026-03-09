from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from statistics import mean
from time import perf_counter
from typing import Any

from app.core.timezone import parse_iso_datetime
from app.models.schemas import SearchEvidenceItem, SearchResultItem
from app.services.capture_metadata_repo import capture_metadata_repo
from app.services.search_service import SearchBuildResult, build_search_results


def _safe_int(value: object) -> int | None:
    try:
        return int(value)
    except Exception:
        return None


def _safe_float(value: object) -> float | None:
    try:
        return float(value)
    except Exception:
        return None


def _safe_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        key = value.strip().lower()
        if key in {"1", "true", "yes", "y"}:
            return True
        if key in {"0", "false", "no", "n"}:
            return False
    return None


def _safe_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _safe_text_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for item in value:
        text = _safe_text(item)
        if text:
            items.append(text)
    return items


def _safe_int_list(value: object) -> list[int]:
    if not isinstance(value, list):
        return []
    items: list[int] = []
    for item in value:
        parsed = _safe_int(item)
        if parsed is not None:
            items.append(parsed)
    return items


@dataclass(frozen=True)
class QuerySource:
    file: str | None = None
    meta_id: int | None = None
    image_path: str | None = None


@dataclass(frozen=True)
class QueryFilters:
    upper_color: str | None = None
    lower_color: str | None = None
    time_start: datetime | None = None
    time_end: datetime | None = None
    has_hat: bool | None = None
    camera_id: str | None = None
    image_mode: str | None = None
    is_night: bool | None = None
    min_quality_score: float | None = None
    pose_hint: str | None = None


@dataclass(frozen=True)
class QueryExpectations:
    target_keys: tuple[str, ...]
    track_ids: tuple[int, ...]


@dataclass(frozen=True)
class EvalCase:
    case_id: str
    notes: str | None
    query: QuerySource
    filters: QueryFilters
    expected: QueryExpectations
    top_k: int
    face_mode: str
    group_by_target: bool
    diverse_camera: bool


@dataclass(frozen=True)
class EvalCaseResult:
    case_id: str
    query_source: str
    expected_target_keys: tuple[str, ...]
    expected_track_ids: tuple[int, ...]
    result_count: int
    elapsed_ms: int
    query_has_face: bool
    face_assist_used: bool
    hit_top1: bool
    hit_top5: bool
    hit_top10: bool
    first_relevant_rank: int | None
    matched_track_id: int | None
    matched_target_key: str | None
    top_results: list[dict[str, Any]]


@dataclass(frozen=True)
class EvalSummary:
    total_cases: int
    cases_with_results: int
    top1_hit_rate: float
    top5_hit_rate: float
    top10_hit_rate: float
    mean_reciprocal_rank: float
    avg_latency_ms: float
    p95_latency_ms: float


@dataclass(frozen=True)
class EvalReport:
    dataset_name: str
    generated_at: str
    summary: EvalSummary
    cases: list[EvalCaseResult]


def _parse_filters(raw: dict[str, Any]) -> QueryFilters:
    return QueryFilters(
        upper_color=_safe_text(raw.get("upper_color")),
        lower_color=_safe_text(raw.get("lower_color")),
        time_start=parse_iso_datetime(raw.get("time_start")),
        time_end=parse_iso_datetime(raw.get("time_end")),
        has_hat=_safe_bool(raw.get("has_hat")),
        camera_id=_safe_text(raw.get("camera_id")),
        image_mode=_safe_text(raw.get("image_mode")),
        is_night=_safe_bool(raw.get("is_night")),
        min_quality_score=_safe_float(raw.get("min_quality_score")),
        pose_hint=_safe_text(raw.get("pose_hint")),
    )


def _parse_case(raw: dict[str, Any], *, defaults: dict[str, Any], idx: int) -> EvalCase:
    case_id = _safe_text(raw.get("id")) or f"case_{idx + 1:03d}"
    query_raw = raw.get("query")
    if not isinstance(query_raw, dict):
        raise ValueError(f"case={case_id} missing query object")
    query = QuerySource(
        file=_safe_text(query_raw.get("file")),
        meta_id=_safe_int(query_raw.get("meta_id")),
        image_path=_safe_text(query_raw.get("image_path")),
    )
    if not any([query.file, query.meta_id, query.image_path]):
        raise ValueError(f"case={case_id} query must contain one of file/meta_id/image_path")

    filters = _parse_filters(raw.get("filters") if isinstance(raw.get("filters"), dict) else {})
    expected_raw = raw.get("expected")
    if not isinstance(expected_raw, dict):
        raise ValueError(f"case={case_id} missing expected object")
    expected = QueryExpectations(
        target_keys=tuple(_safe_text_list(expected_raw.get("target_keys"))),
        track_ids=tuple(_safe_int_list(expected_raw.get("track_ids"))),
    )
    if not expected.target_keys and not expected.track_ids:
        raise ValueError(f"case={case_id} expected target_keys or track_ids is required")

    top_k = _safe_int(raw.get("top_k"))
    if top_k is None:
        top_k = _safe_int(defaults.get("top_k")) or 10

    face_mode = _safe_text(raw.get("face_mode")) or _safe_text(defaults.get("face_mode")) or "assist"
    group_by_target = _safe_bool(raw.get("group_by_target"))
    if group_by_target is None:
        group_by_target = _safe_bool(defaults.get("group_by_target"))
    diverse_camera = _safe_bool(raw.get("diverse_camera"))
    if diverse_camera is None:
        diverse_camera = _safe_bool(defaults.get("diverse_camera"))

    return EvalCase(
        case_id=case_id,
        notes=_safe_text(raw.get("notes")),
        query=query,
        filters=filters,
        expected=expected,
        top_k=max(1, min(100, top_k)),
        face_mode=face_mode.strip().lower() or "assist",
        group_by_target=True if group_by_target is None else bool(group_by_target),
        diverse_camera=True if diverse_camera is None else bool(diverse_camera),
    )


def load_eval_cases(dataset_path: Path) -> tuple[str, list[EvalCase]]:
    payload = json.loads(dataset_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("dataset root must be a JSON object")
    name = _safe_text(payload.get("name")) or dataset_path.stem
    defaults = payload.get("defaults") if isinstance(payload.get("defaults"), dict) else {}
    cases_raw = payload.get("cases")
    if not isinstance(cases_raw, list) or not cases_raw:
        raise ValueError("dataset must contain non-empty cases list")
    cases = [_parse_case(item, defaults=defaults, idx=idx) for idx, item in enumerate(cases_raw) if isinstance(item, dict)]
    if not cases:
        raise ValueError("dataset does not contain valid cases")
    return name, cases


def _resolve_query_bytes(case: EvalCase, *, dataset_dir: Path) -> tuple[bytes, str]:
    if case.query.meta_id is not None:
        found = capture_metadata_repo.get_photo_by_track_id(case.query.meta_id)
        if not found:
            raise FileNotFoundError(f"case={case.case_id} query meta_id={case.query.meta_id} not found in capture_metadata")
        return found[0], f"meta_id:{case.query.meta_id}"
    if case.query.image_path is not None:
        found = capture_metadata_repo.get_photo(case.query.image_path)
        if not found:
            raise FileNotFoundError(f"case={case.case_id} query image_path not found: {case.query.image_path}")
        return found[0], f"image_path:{case.query.image_path}"
    if case.query.file is None:
        raise FileNotFoundError(f"case={case.case_id} missing query file")
    file_path = Path(case.query.file)
    if not file_path.is_absolute():
        file_path = (dataset_dir / file_path).resolve()
    if not file_path.exists():
        raise FileNotFoundError(f"case={case.case_id} query file not found: {file_path}")
    return file_path.read_bytes(), str(file_path)


def _candidate_matches(
    item: SearchResultItem | SearchEvidenceItem,
    *,
    expected_track_ids: set[int],
    expected_target_keys: set[str],
) -> tuple[bool, int | None, str | None]:
    track_id = int(item.track_id)
    target_key = (item.target_key or "").strip()
    if track_id in expected_track_ids:
        return True, track_id, target_key or None
    if target_key and target_key in expected_target_keys:
        return True, track_id, target_key
    return False, None, None


def _find_first_relevant(
    items: list[SearchResultItem],
    *,
    expected_track_ids: set[int],
    expected_target_keys: set[str],
) -> tuple[int | None, int | None, str | None]:
    for idx, item in enumerate(items, start=1):
        matched, track_id, target_key = _candidate_matches(
            item,
            expected_track_ids=expected_track_ids,
            expected_target_keys=expected_target_keys,
        )
        if matched:
            return idx, track_id, target_key
        for evidence in item.evidence:
            matched, track_id, target_key = _candidate_matches(
                evidence,
                expected_track_ids=expected_track_ids,
                expected_target_keys=expected_target_keys,
            )
            if matched:
                return idx, track_id, target_key
    return None, None, None


def _compact_results(items: list[SearchResultItem], limit: int = 5) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in items[:limit]:
        out.append(
            {
                "track_id": int(item.track_id),
                "target_key": item.target_key,
                "similarity": float(item.similarity),
                "camera_id": item.camera_id,
                "start_time": item.start_time.isoformat(),
                "evidence_count": int(item.evidence_count),
            }
        )
    return out


def evaluate_case(case: EvalCase, *, dataset_dir: Path) -> EvalCaseResult:
    image_bytes, query_source = _resolve_query_bytes(case, dataset_dir=dataset_dir)
    started = perf_counter()
    built: SearchBuildResult = build_search_results(
        image_bytes=image_bytes,
        top_k=case.top_k,
        upper_color=case.filters.upper_color,
        lower_color=case.filters.lower_color,
        time_start=case.filters.time_start,
        time_end=case.filters.time_end,
        has_hat=case.filters.has_hat,
        camera_id=case.filters.camera_id,
        image_mode=case.filters.image_mode,
        is_night=case.filters.is_night,
        min_quality_score=case.filters.min_quality_score,
        pose_hint=case.filters.pose_hint,
        face_mode=case.face_mode,
        group_by_target=case.group_by_target,
        diverse_camera=case.diverse_camera,
        now=datetime.now(timezone.utc),
    )
    elapsed_ms = int((perf_counter() - started) * 1000)
    expected_track_ids = set(case.expected.track_ids)
    expected_target_keys = {item.strip() for item in case.expected.target_keys if item.strip()}
    first_rank, matched_track_id, matched_target_key = _find_first_relevant(
        built.items,
        expected_track_ids=expected_track_ids,
        expected_target_keys=expected_target_keys,
    )
    return EvalCaseResult(
        case_id=case.case_id,
        query_source=query_source,
        expected_target_keys=case.expected.target_keys,
        expected_track_ids=case.expected.track_ids,
        result_count=len(built.items),
        elapsed_ms=elapsed_ms,
        query_has_face=built.query_has_face,
        face_assist_used=built.face_assist_used,
        hit_top1=first_rank == 1,
        hit_top5=first_rank is not None and first_rank <= 5,
        hit_top10=first_rank is not None and first_rank <= 10,
        first_relevant_rank=first_rank,
        matched_track_id=matched_track_id,
        matched_target_key=matched_target_key,
        top_results=_compact_results(built.items),
    )


def summarize_cases(cases: list[EvalCaseResult]) -> EvalSummary:
    total = len(cases)
    if total == 0:
        return EvalSummary(
            total_cases=0,
            cases_with_results=0,
            top1_hit_rate=0.0,
            top5_hit_rate=0.0,
            top10_hit_rate=0.0,
            mean_reciprocal_rank=0.0,
            avg_latency_ms=0.0,
            p95_latency_ms=0.0,
        )

    latencies = sorted(float(item.elapsed_ms) for item in cases)
    p95_idx = max(0, min(len(latencies) - 1, int(round((len(latencies) - 1) * 0.95))))
    reciprocal_ranks = [1.0 / float(item.first_relevant_rank) for item in cases if item.first_relevant_rank]
    return EvalSummary(
        total_cases=total,
        cases_with_results=sum(1 for item in cases if item.result_count > 0),
        top1_hit_rate=round(sum(1 for item in cases if item.hit_top1) / float(total), 6),
        top5_hit_rate=round(sum(1 for item in cases if item.hit_top5) / float(total), 6),
        top10_hit_rate=round(sum(1 for item in cases if item.hit_top10) / float(total), 6),
        mean_reciprocal_rank=round(mean(reciprocal_ranks), 6) if reciprocal_ranks else 0.0,
        avg_latency_ms=round(mean(latencies), 3),
        p95_latency_ms=round(latencies[p95_idx], 3),
    )


def build_report(dataset_name: str, cases: list[EvalCaseResult]) -> EvalReport:
    return EvalReport(
        dataset_name=dataset_name,
        generated_at=datetime.now(timezone.utc).isoformat(),
        summary=summarize_cases(cases),
        cases=cases,
    )


def report_to_dict(report: EvalReport) -> dict[str, Any]:
    return {
        "dataset_name": report.dataset_name,
        "generated_at": report.generated_at,
        "summary": {
            "total_cases": report.summary.total_cases,
            "cases_with_results": report.summary.cases_with_results,
            "top1_hit_rate": report.summary.top1_hit_rate,
            "top5_hit_rate": report.summary.top5_hit_rate,
            "top10_hit_rate": report.summary.top10_hit_rate,
            "mean_reciprocal_rank": report.summary.mean_reciprocal_rank,
            "avg_latency_ms": report.summary.avg_latency_ms,
            "p95_latency_ms": report.summary.p95_latency_ms,
        },
        "cases": [
            {
                "case_id": item.case_id,
                "query_source": item.query_source,
                "expected_target_keys": list(item.expected_target_keys),
                "expected_track_ids": list(item.expected_track_ids),
                "result_count": item.result_count,
                "elapsed_ms": item.elapsed_ms,
                "query_has_face": item.query_has_face,
                "face_assist_used": item.face_assist_used,
                "hit_top1": item.hit_top1,
                "hit_top5": item.hit_top5,
                "hit_top10": item.hit_top10,
                "first_relevant_rank": item.first_relevant_rank,
                "matched_track_id": item.matched_track_id,
                "matched_target_key": item.matched_target_key,
                "top_results": item.top_results,
            }
            for item in report.cases
        ],
    }
