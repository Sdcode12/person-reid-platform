from __future__ import annotations

import logging
from datetime import datetime, timezone
from time import perf_counter
from uuid import uuid4

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, UploadFile

from app.api.v1.deps import AuthUser, require_permission
from app.core.image_mode import IMAGE_MODE_ALL, normalize_image_mode
from app.core.timezone import ensure_aware
from app.models.schemas import SearchFeedbackRequest, SearchHistoryResponse, SearchResponse, SearchResultItem
from app.services.search_feedback_repo import search_feedback_repo
from app.services.search_query_repo import search_query_repo
from app.services.search_service import build_search_results

router = APIRouter()
_POSE_HINT_ALLOWED = {"front_or_back", "side", "partial_or_close"}
_FACE_MODE_ALLOWED = {"off", "assist"}
logger = logging.getLogger(__name__)


@router.post("/search", response_model=SearchResponse)
async def search(
    image: UploadFile = File(...),
    upper_color: str | None = Form(default=None),
    lower_color: str | None = Form(default=None),
    time_start: datetime | None = Form(default=None),
    time_end: datetime | None = Form(default=None),
    camera_id: str | None = Form(default=None),
    image_mode: str | None = Form(default=None),
    is_night: bool | None = Form(default=None),
    min_quality_score: float | None = Form(default=None),
    pose_hint: str | None = Form(default=None),
    gender: str | None = Form(default=None),
    has_hat: bool | None = Form(default=None),
    has_backpack: bool | None = Form(default=None),
    is_cycling: bool | None = Form(default=None),
    sleeve_length: str | None = Form(default=None),
    face_mode: str = Form(default="assist"),
    group_by_target: bool = Form(default=True),
    diverse_camera: bool = Form(default=True),
    top_k: int = Form(default=10),
    user: AuthUser = Depends(require_permission("search:run")),
) -> SearchResponse:
    _ = (gender, has_backpack, is_cycling, sleeve_length)

    if top_k < 1 or top_k > 100:
        raise HTTPException(status_code=422, detail="top_k must be between 1 and 100")
    if min_quality_score is not None and (min_quality_score < 0.0 or min_quality_score > 1.0):
        raise HTTPException(status_code=422, detail="min_quality_score must be between 0 and 1")
    if pose_hint and pose_hint.strip().lower() not in _POSE_HINT_ALLOWED:
        raise HTTPException(status_code=422, detail="invalid pose_hint")
    raw_image_mode = image_mode
    image_mode = normalize_image_mode(raw_image_mode)
    if raw_image_mode is not None and image_mode is None:
        raise HTTPException(status_code=422, detail=f"invalid image_mode, allowed={','.join(IMAGE_MODE_ALL)}")
    face_mode = (face_mode or "assist").strip().lower()
    if face_mode not in _FACE_MODE_ALLOWED:
        raise HTTPException(status_code=422, detail="invalid face_mode")

    content = await image.read()
    if not content:
        raise HTTPException(status_code=400, detail="empty image")

    if time_start and time_start.tzinfo is None:
        time_start = ensure_aware(time_start)
    if time_end and time_end.tzinfo is None:
        time_end = ensure_aware(time_end)
    if time_start and time_end and time_start > time_end:
        raise HTTPException(status_code=422, detail="time_start must be <= time_end")

    started = perf_counter()
    query_id = str(uuid4())
    now = datetime.now(timezone.utc)

    built = build_search_results(
        image_bytes=content,
        top_k=top_k,
        upper_color=upper_color,
        lower_color=lower_color,
        time_start=time_start,
        time_end=time_end,
        has_hat=has_hat,
        camera_id=camera_id,
        image_mode=image_mode,
        is_night=is_night,
        min_quality_score=min_quality_score,
        pose_hint=pose_hint,
        face_mode=face_mode,
        group_by_target=group_by_target,
        diverse_camera=diverse_camera,
        now=now,
    )
    elapsed_ms = int((perf_counter() - started) * 1000)
    items: list[SearchResultItem] = built.items
    reduction_rate = 0.0
    if built.layer1_count > 0:
        reduction_rate = max(0.0, min(1.0, 1.0 - (built.layer2_count / float(built.layer1_count))))

    response = SearchResponse(
        query_id=query_id,
        strategy=built.strategy,
        count=len(items),
        elapsed_ms=elapsed_ms,
        funnel={
            "layer1_count": built.layer1_count,
            "filtered_count": built.filtered_count,
            "layer2_count": built.layer2_count,
            "pre_rerank_count": built.pre_rerank_count,
            "layer3_count": len(items),
        },
        metrics={
            "candidate_reduction_rate": round(reduction_rate, 6),
            "recall_at_10": round(min(1.0, len(items) / 10.0), 6),
            "fpr": 0.0,
            "p95_latency_ms": float(elapsed_ms),
            "query_has_face": 1.0 if built.query_has_face else 0.0,
            "face_assist_used": 1.0 if built.face_assist_used else 0.0,
            "reranked_count": float(built.reranked_count),
            "filtered_count": float(built.filtered_count),
            "pre_rerank_count": float(built.pre_rerank_count),
            "db_ms": float(built.timings_ms.get("db_ms", 0)),
            "pre_rank_ms": float(built.timings_ms.get("pre_rank_ms", 0)),
            "asset_load_ms": float(built.timings_ms.get("asset_load_ms", 0)),
            "heavy_rerank_ms": float(built.timings_ms.get("heavy_rerank_ms", 0)),
            "group_ms": float(built.timings_ms.get("group_ms", 0)),
        },
        timings_ms=built.timings_ms,
        timeline=built.timeline,
        results=items,
    )
    try:
        search_query_repo.insert_query(
            query_id=query_id,
            created_by=user.username,
            upper_color=upper_color,
            lower_color=lower_color,
            time_start=time_start,
            time_end=time_end,
            camera_id=camera_id,
            image_mode=image_mode,
            has_hat=has_hat,
            pose_hint=pose_hint,
            min_quality_score=min_quality_score,
            face_mode=face_mode,
            group_by_target=group_by_target,
            diverse_camera=diverse_camera,
            top_k=top_k,
            result_count=len(items),
            elapsed_ms=elapsed_ms,
            funnel=response.funnel,
            metrics=response.metrics,
        )
    except Exception:  # noqa: BLE001
        logger.exception("failed to persist search query history query_id=%s", query_id)
    return response


@router.get("/search/history", response_model=SearchHistoryResponse)
def search_history(
    limit: int = 12,
    all_users: bool = False,
    user: AuthUser = Depends(require_permission("search:run")),
) -> SearchHistoryResponse:
    if limit < 1 or limit > 100:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 100")
    if all_users and user.role != "admin":
        raise HTTPException(status_code=403, detail="permission denied")

    try:
        items = search_query_repo.list_queries(
            limit=limit,
            created_by=None if all_users else user.username,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"failed to load search history: {exc}") from exc
    return SearchHistoryResponse(items=items)


@router.post("/search/{query_id}/feedback")
def feedback(
    query_id: str,
    body: SearchFeedbackRequest = Body(...),
    user: AuthUser = Depends(require_permission("search:feedback:write")),
) -> dict[str, str | int | None]:
    if body.verdict not in {"hit", "miss"}:
        raise HTTPException(status_code=422, detail="verdict must be hit or miss")

    try:
        inserted = search_feedback_repo.insert_feedback(
            query_id=query_id,
            track_id=body.track_id,
            verdict=body.verdict,
            note=body.note,
            created_by=user.username,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"failed to store search feedback: {exc}") from exc

    return {
        "query_id": query_id,
        "track_id": body.track_id,
        "verdict": body.verdict,
        "note": body.note,
        "status": "stored",
        "feedback_id": inserted.get("feedback_id"),
        "created_at": inserted.get("created_at"),
    }
