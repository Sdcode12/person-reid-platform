from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from math import exp
import json
import os
from pathlib import Path
from time import perf_counter
from typing import Any
import zlib

import cv2
import numpy as np

from app.core.constants import Color
from app.core.color_analysis import dominant_color
from app.core.detector import Detection, PersonDetector, ReIDEmbeddingExtractor
from app.core.image_mode import IMAGE_MODE_COLOR, IMAGE_MODE_LOW_LIGHT_COLOR, infer_image_mode, normalize_image_mode
from app.core.timezone import ensure_aware, parse_iso_datetime
from app.models.schemas import SearchEvidenceItem, SearchResultItem
from app.services.capture_control_service import capture_control_service
from app.services.capture_metadata_repo import capture_metadata_repo


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except Exception:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except Exception:
        return default


_SEARCH_REID_EXTRACTOR = ReIDEmbeddingExtractor(
    mode=os.getenv("REID_SEARCH_MODE", "auto"),
    model_path=os.getenv("REID_SEARCH_MODEL_PATH", "models/person_reid.onnx"),
    input_width=_env_int("REID_SEARCH_INPUT_WIDTH", 128),
    input_height=_env_int("REID_SEARCH_INPUT_HEIGHT", 256),
    output_dim=512,
)

_SEARCH_PERSON_DETECTOR = PersonDetector(
    mode=os.getenv("SEARCH_PERSON_DETECTOR_MODE", "auto"),
    hit_threshold=_env_float("SEARCH_HOG_HIT_THRESHOLD", 0.0),
    yolo_model_path=os.getenv("SEARCH_YOLO_MODEL_PATH", "models/yolov8n.onnx"),
    yolo_input_size=_env_int("SEARCH_YOLO_INPUT_SIZE", 640),
    confidence_threshold=_env_float("SEARCH_YOLO_CONFIDENCE_THRESHOLD", 0.25),
    nms_threshold=_env_float("SEARCH_YOLO_NMS_THRESHOLD", 0.45),
    yolo_person_class_ids=[0],
)

_SEARCH_DB_CANDIDATE_MIN = max(60, _env_int("SEARCH_DB_CANDIDATE_MIN", 120))
_SEARCH_DB_CANDIDATE_FACTOR = max(6, _env_int("SEARCH_DB_CANDIDATE_FACTOR", 12))
_SEARCH_DB_CANDIDATE_MAX = max(_SEARCH_DB_CANDIDATE_MIN, _env_int("SEARCH_DB_CANDIDATE_MAX", 180))
_SEARCH_PRE_RERANK_MIN = max(20, _env_int("SEARCH_PRE_RERANK_MIN", 40))
_SEARCH_PRE_RERANK_FACTOR = max(2, _env_int("SEARCH_PRE_RERANK_FACTOR", 5))
_SEARCH_PRE_RERANK_MAX = max(_SEARCH_PRE_RERANK_MIN, _env_int("SEARCH_PRE_RERANK_MAX", 60))
_SEARCH_RERANK_FACTOR = max(2, _env_int("SEARCH_RERANK_FACTOR", 3))
_SEARCH_RERANK_MAX = max(12, _env_int("SEARCH_RERANK_MAX", 36))
_SEARCH_EXACT_FILTER_THRESHOLD = max(500, _env_int("SEARCH_EXACT_FILTER_THRESHOLD", 2500))
_SEARCH_ANN_EF_SEARCH = max(40, _env_int("SEARCH_ANN_EF_SEARCH", 100))

_FACE_MODE_OFF = "off"
_FACE_MODE_ASSIST = "assist"


def _load_face_cascade() -> cv2.CascadeClassifier | None:
    base = getattr(cv2.data, "haarcascades", "")
    if not base:
        return None
    path = Path(base) / "haarcascade_frontalface_default.xml"
    if not path.exists():
        return None
    cascade = cv2.CascadeClassifier(str(path))
    if cascade.empty():
        return None
    return cascade


_FACE_CASCADE = _load_face_cascade()


@dataclass
class QueryVisualFeatures:
    upper_color: str | None
    upper_color_conf: float
    lower_color: str | None
    lower_color_conf: float
    body_embedding: list[float]
    upper_embedding: list[float]
    lower_embedding: list[float]
    face_embedding: list[float]
    face_confidence: float
    image_mode: str
    quality_score: float
    person_area_ratio: float
    body_confidence: float
    image_hash: int | None
    upper_visibility: float
    lower_visibility: float
    detection_used: bool

    @property
    def has_face(self) -> bool:
        return bool(self.face_embedding)


@dataclass
class CandidateVisualFeatures:
    upper_embedding: list[float]
    lower_embedding: list[float]
    face_embedding: list[float]
    face_confidence: float
    person_area_ratio: float | None
    image_mode: str | None
    quality_score: float
    upper_visibility: float
    lower_visibility: float

    @property
    def has_face(self) -> bool:
        return bool(self.face_embedding)


@dataclass
class RankedCandidate:
    source: dict[str, Any]
    track_id: int
    captured_at: datetime
    camera_id: str
    target_key: str | None
    image_path: str | None
    upper_color: str
    lower_color: str
    has_hat: bool | None
    image_mode: str | None
    is_night: bool | None
    pose_hint: str | None
    quality_score: float | None
    body_score: float
    attr_score: float
    time_score: float
    preliminary_score: float
    upper_sim: float | None = None
    lower_sim: float | None = None
    face_sim: float | None = None
    face_used: bool = False
    face_available: bool = False
    final_score: float = 0.0


def _safe_color(color: str | None) -> str | None:
    if not color:
        return None
    key = color.strip().lower()
    if key not in Color.ALL:
        return None
    if key == Color.UNKNOWN:
        return None
    return key


def _parse_dt(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return ensure_aware(value)
    return parse_iso_datetime(value)


def _to_float_list(value: object) -> list[float]:
    if not isinstance(value, list):
        return []
    out: list[float] = []
    for item in value:
        try:
            out.append(float(item))
        except Exception:
            return []
    return out


def _to_float(value: object) -> float | None:
    try:
        v = float(value)
    except Exception:
        return None
    if not np.isfinite(v):
        return None
    return v


def _to_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        key = value.strip().lower()
        if key in {"1", "true", "yes", "y"}:
            return True
        if key in {"0", "false", "no", "n"}:
            return False
    return None


def _as_dict(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _normalize_dense_vector(vec: np.ndarray) -> list[float]:
    flat = np.asarray(vec, dtype=np.float32).reshape(-1)
    if flat.size == 0:
        return []
    denom = float(np.linalg.norm(flat))
    if denom > 1e-12:
        flat = flat / denom
    return [round(float(v), 6) for v in flat.tolist()]


def _cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0
    a = np.asarray(vec_a, dtype=np.float32)
    b = np.asarray(vec_b, dtype=np.float32)
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom <= 1e-12:
        return 0.0
    score = float(np.dot(a, b) / denom)
    return max(0.0, min(1.0, score))


def _crop(frame: np.ndarray, bbox: tuple[int, int, int, int]) -> np.ndarray:
    x, y, w, h = bbox
    frame_h, frame_w = frame.shape[:2]
    x1 = max(0, min(int(x), frame_w - 1))
    y1 = max(0, min(int(y), frame_h - 1))
    x2 = max(x1 + 1, min(int(x + w), frame_w))
    y2 = max(y1 + 1, min(int(y + h), frame_h))
    return frame[y1:y2, x1:x2]


def _parse_bbox(raw: object) -> tuple[int, int, int, int] | None:
    if not isinstance(raw, (list, tuple)) or len(raw) < 4:
        return None
    try:
        x = int(raw[0])
        y = int(raw[1])
        w = int(raw[2])
        h = int(raw[3])
    except Exception:
        return None
    if w <= 1 or h <= 1:
        return None
    return x, y, w, h


def _pick_primary_detection(frame: np.ndarray) -> Detection | None:
    detections = _SEARCH_PERSON_DETECTOR.detect(frame)
    if not detections:
        return None
    detections.sort(key=lambda det: det.bbox[2] * det.bbox[3], reverse=True)
    return detections[0]


def _split_person_regions(person_crop: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    if person_crop.size == 0:
        return person_crop, person_crop
    height = int(person_crop.shape[0])
    split = max(1, int(height * 0.55))
    upper = person_crop[:split, :]
    lower = person_crop[split:, :] if split < height else person_crop[max(0, height - 1) :, :]
    return upper, lower


def _score_sharpness(image: np.ndarray) -> float:
    if image.size == 0:
        return 0.0
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    lap_var = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    return max(0.0, min(1.0, lap_var / 180.0))


def _score_contrast(image: np.ndarray) -> float:
    if image.size == 0:
        return 0.0
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    std = float(gray.std())
    return max(0.0, min(1.0, std / 52.0))


def _score_exposure(image: np.ndarray) -> float:
    if image.size == 0:
        return 0.0
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    mean_value = float(gray.mean())
    distance = abs(mean_value - 128.0)
    return max(0.0, min(1.0, 1.0 - (distance / 128.0)))


def _estimate_visual_quality(image: np.ndarray) -> float:
    if image.size == 0:
        return 0.0
    sharpness = _score_sharpness(image)
    contrast = _score_contrast(image)
    exposure = _score_exposure(image)
    return max(0.0, min(1.0, 0.55 * sharpness + 0.25 * contrast + 0.20 * exposure))


def _bbox_area_ratio(frame: np.ndarray, bbox: tuple[int, int, int, int] | None) -> float:
    if frame is None or frame.size == 0 or bbox is None:
        return 0.0
    frame_h, frame_w = frame.shape[:2]
    if frame_h <= 0 or frame_w <= 0:
        return 0.0
    _, _, w, h = bbox
    if w <= 0 or h <= 0:
        return 0.0
    return max(0.0, min(1.0, (float(w) * float(h)) / float(frame_h * frame_w)))


def _region_visibility(region: np.ndarray, full_crop: np.ndarray) -> float:
    if region.size == 0 or full_crop.size == 0:
        return 0.0
    region_h, region_w = region.shape[:2]
    height_score = max(0.0, min(1.0, float(region_h) / 96.0))
    width_score = max(0.0, min(1.0, float(region_w) / 48.0))
    area_score = max(0.0, min(1.0, float(region_h * region_w) / 8192.0))
    quality_score = _estimate_visual_quality(region)
    return max(0.0, min(1.0, 0.35 * height_score + 0.20 * width_score + 0.20 * area_score + 0.25 * quality_score))


def _dhash(image: np.ndarray, hash_size: int = 8) -> int | None:
    if image is None or image.size == 0:
        return None
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    resized = cv2.resize(gray, (hash_size + 1, hash_size), interpolation=cv2.INTER_AREA)
    diff = resized[:, 1:] > resized[:, :-1]
    value = 0
    for bit in diff.flatten():
        value = (value << 1) | int(bool(bit))
    return int(value)


def _hash_similarity(query_hash: int | None, candidate_hash_hex: str | None) -> float:
    if query_hash is None or not candidate_hash_hex:
        return 0.0
    text = candidate_hash_hex.strip().lower()
    if not text:
        return 0.0
    try:
        candidate_hash = int(text, 16)
    except Exception:
        return 0.0
    hamming = (query_hash ^ candidate_hash).bit_count()
    similarity = 1.0 - (float(hamming) / 64.0)
    return max(0.0, min(1.0, similarity))


def _extract_region_embedding(region: np.ndarray) -> list[float]:
    if region.size == 0 or min(region.shape[:2]) < 24:
        return []
    return _SEARCH_REID_EXTRACTOR.extract(region)


def _face_signature(face_crop: np.ndarray) -> list[float]:
    if face_crop.size == 0:
        return []
    gray = cv2.cvtColor(face_crop, cv2.COLOR_BGR2GRAY)
    gray = cv2.equalizeHist(gray)
    resized = cv2.resize(gray, (48, 48), interpolation=cv2.INTER_AREA)
    dct = cv2.dct(resized.astype(np.float32) / 255.0)
    low_freq = dct[:12, :12].flatten()
    hist = cv2.calcHist([resized], [0], None, [16], [0, 256]).flatten().astype(np.float32)
    hist_sum = float(hist.sum())
    if hist_sum > 1e-12:
        hist /= hist_sum
    raw = np.concatenate([low_freq.astype(np.float32), hist], axis=0)
    return _normalize_dense_vector(raw)


def _extract_face_features(person_crop: np.ndarray) -> tuple[list[float], float]:
    if _FACE_CASCADE is None or person_crop.size == 0:
        return [], 0.0
    gray = cv2.cvtColor(person_crop, cv2.COLOR_BGR2GRAY)
    faces = _FACE_CASCADE.detectMultiScale(
        gray,
        scaleFactor=1.1,
        minNeighbors=5,
        minSize=(24, 24),
    )
    if faces is None or len(faces) == 0:
        return [], 0.0
    faces_sorted = sorted(faces, key=lambda face: int(face[2]) * int(face[3]), reverse=True)
    x, y, w, h = [int(v) for v in faces_sorted[0]]
    if w <= 8 or h <= 8:
        return [], 0.0
    face_crop = _crop(person_crop, (x, y, w, h))
    if face_crop.size == 0:
        return [], 0.0
    signature = _face_signature(face_crop)
    if not signature:
        return [], 0.0
    face_area = max(1, w * h)
    crop_area = max(1, int(person_crop.shape[0]) * int(person_crop.shape[1]))
    area_score = max(0.0, min(1.0, (face_area / float(crop_area)) / 0.12))
    lap_var = float(cv2.Laplacian(gray[y : y + h, x : x + w], cv2.CV_64F).var())
    sharp_score = max(0.0, min(1.0, lap_var / 120.0))
    confidence = max(0.0, min(1.0, 0.55 * area_score + 0.45 * sharp_score))
    if confidence < 0.08:
        return [], 0.0
    return signature, confidence


def _safe_color_conf(raw: object) -> float:
    value = _to_float(raw)
    if value is None:
        return 0.5
    return max(0.0, min(1.0, value))


def _item_image_mode(item: dict[str, Any], raw: dict[str, Any]) -> str:
    mode = normalize_image_mode(item.get("image_mode"))
    if mode:
        return mode
    mode = normalize_image_mode(raw.get("image_mode"))
    if mode:
        return mode
    is_night = _to_bool(item.get("is_night"))
    if is_night is None:
        is_night = _to_bool(raw.get("is_night"))
    if is_night is None:
        return "unknown"
    return IMAGE_MODE_LOW_LIGHT_COLOR if is_night else IMAGE_MODE_COLOR


def _extract_query_features(image_bytes: bytes) -> QueryVisualFeatures:
    empty = QueryVisualFeatures(
        upper_color=None,
        upper_color_conf=0.0,
        lower_color=None,
        lower_color_conf=0.0,
        body_embedding=[],
        upper_embedding=[],
        lower_embedding=[],
        face_embedding=[],
        face_confidence=0.0,
        image_mode="unknown",
        quality_score=0.0,
        person_area_ratio=0.0,
        body_confidence=0.0,
        image_hash=None,
        upper_visibility=0.0,
        lower_visibility=0.0,
        detection_used=False,
    )
    if not image_bytes:
        return empty
    frame = cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
    if frame is None or frame.size == 0:
        return empty

    detection = _pick_primary_detection(frame)
    person_area_ratio = _bbox_area_ratio(frame, detection.bbox if detection is not None else None)
    body_confidence = max(0.0, min(1.0, float(detection.confidence))) if detection is not None else 0.0
    person_crop = _crop(frame, detection.bbox) if detection is not None else frame
    if person_crop.size == 0:
        person_crop = frame

    upper_crop, lower_crop = _split_person_regions(person_crop)
    upper_visibility = _region_visibility(upper_crop, person_crop)
    lower_visibility = _region_visibility(lower_crop, person_crop)
    upper_color, upper_conf = dominant_color(upper_crop)
    lower_color, lower_conf = dominant_color(lower_crop)
    body_embedding = _SEARCH_REID_EXTRACTOR.extract(person_crop)
    upper_embedding = _extract_region_embedding(upper_crop)
    lower_embedding = _extract_region_embedding(lower_crop)
    face_embedding, face_confidence = _extract_face_features(person_crop)
    return QueryVisualFeatures(
        upper_color=_safe_color(upper_color),
        upper_color_conf=upper_conf,
        lower_color=_safe_color(lower_color),
        lower_color_conf=lower_conf,
        body_embedding=body_embedding,
        upper_embedding=upper_embedding,
        lower_embedding=lower_embedding,
        face_embedding=face_embedding,
        face_confidence=face_confidence,
        image_mode=infer_image_mode(frame),
        quality_score=_estimate_visual_quality(person_crop),
        person_area_ratio=person_area_ratio,
        body_confidence=body_confidence,
        image_hash=_dhash(person_crop),
        upper_visibility=upper_visibility,
        lower_visibility=lower_visibility,
        detection_used=detection is not None,
    )


def _decode_candidate_frame(item: dict[str, Any]) -> np.ndarray | None:
    image_bytes = item.get("image_bytes")
    payload: bytes | None = None
    if isinstance(image_bytes, memoryview):
        payload = bytes(image_bytes)
    elif isinstance(image_bytes, (bytes, bytearray)):
        payload = bytes(image_bytes)
    if payload:
        frame = cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_COLOR)
        if frame is not None and frame.size > 0:
            return frame

    image_path = str(item.get("image_path", "")).strip()
    if not image_path or image_path.startswith("db://"):
        return None
    try:
        path = Path(image_path).expanduser().resolve()
    except Exception:
        return None
    if not path.exists() or not path.is_file():
        return None
    frame = cv2.imread(str(path))
    if frame is None or frame.size == 0:
        return None
    return frame


def _extract_candidate_features(item: dict[str, Any], *, use_face: bool) -> CandidateVisualFeatures:
    stored_upper = _to_float_list(item.get("upper_embedding"))
    stored_lower = _to_float_list(item.get("lower_embedding"))
    stored_face = _to_float_list(item.get("face_embedding")) if use_face else []
    stored_face_conf = _to_float(item.get("face_confidence")) or 0.0
    stored_quality = _to_float(item.get("quality_score")) or 0.0
    stored_area_ratio = _to_float(item.get("person_area_ratio"))
    stored_mode = _item_image_mode(item, _as_dict(item.get("raw")))
    if stored_upper and stored_lower:
        return CandidateVisualFeatures(
            upper_embedding=stored_upper,
            lower_embedding=stored_lower,
            face_embedding=stored_face,
            face_confidence=stored_face_conf,
            person_area_ratio=stored_area_ratio,
            image_mode=stored_mode,
            quality_score=stored_quality,
            upper_visibility=1.0 if stored_upper else 0.0,
            lower_visibility=1.0 if stored_lower else 0.0,
        )

    frame = _decode_candidate_frame(item)
    if frame is None or frame.size == 0:
        return CandidateVisualFeatures(
            upper_embedding=[],
            lower_embedding=[],
            face_embedding=[],
            face_confidence=0.0,
            person_area_ratio=stored_area_ratio,
            image_mode=stored_mode,
            quality_score=stored_quality,
            upper_visibility=0.0,
            lower_visibility=0.0,
        )

    raw = _as_dict(item.get("raw"))
    if not raw:
        raw = item
    bbox = _parse_bbox(raw.get("person_bbox"))
    if bbox is None:
        detection = _pick_primary_detection(frame)
        bbox = detection.bbox if detection is not None else None
    person_crop = _crop(frame, bbox) if bbox is not None else frame
    if person_crop.size == 0:
        person_crop = frame

    upper_crop, lower_crop = _split_person_regions(person_crop)
    upper_embedding = _extract_region_embedding(upper_crop)
    lower_embedding = _extract_region_embedding(lower_crop)
    if use_face:
        face_embedding, face_confidence = _extract_face_features(person_crop)
    else:
        face_embedding, face_confidence = [], 0.0
    area_ratio = _to_float(item.get("person_area_ratio"))
    if area_ratio is None:
        area_ratio = _to_float(raw.get("person_area_ratio"))
    return CandidateVisualFeatures(
        upper_embedding=upper_embedding,
        lower_embedding=lower_embedding,
        face_embedding=face_embedding,
        face_confidence=face_confidence,
        person_area_ratio=area_ratio,
        image_mode=infer_image_mode(frame),
        quality_score=_estimate_visual_quality(person_crop),
        upper_visibility=_region_visibility(upper_crop, person_crop),
        lower_visibility=_region_visibility(lower_crop, person_crop),
    )


def extract_search_backfill_features(item: dict[str, Any], *, use_face: bool = True) -> dict[str, Any]:
    frame = _decode_candidate_frame(item)
    if frame is None or frame.size == 0:
        return {}

    raw = _as_dict(item.get("raw"))
    if not raw:
        raw = item
    bbox = _parse_bbox(raw.get("person_bbox"))
    if bbox is None:
        detection = _pick_primary_detection(frame)
        bbox = detection.bbox if detection is not None else None
    person_crop = _crop(frame, bbox) if bbox is not None else frame
    if person_crop.size == 0:
        person_crop = frame

    upper_crop, lower_crop = _split_person_regions(person_crop)
    upper_color, upper_color_conf = dominant_color(upper_crop)
    lower_color, lower_color_conf = dominant_color(lower_crop)
    face_embedding: list[float] = []
    face_confidence = 0.0
    if use_face:
        face_embedding, face_confidence = _extract_face_features(person_crop)
    area_ratio = _to_float(item.get("person_area_ratio"))
    if area_ratio is None:
        area_ratio = _to_float(raw.get("person_area_ratio"))
    if area_ratio is None and bbox is not None:
        area_ratio = _bbox_area_ratio(frame, bbox)
    return {
        "upper_embedding": _extract_region_embedding(upper_crop),
        "lower_embedding": _extract_region_embedding(lower_crop),
        "face_embedding": face_embedding,
        "face_confidence": face_confidence,
        "quality_score": _estimate_visual_quality(person_crop),
        "person_area_ratio": area_ratio,
        "image_mode": infer_image_mode(frame),
        "upper_color": _safe_color(upper_color) or Color.UNKNOWN,
        "upper_color_conf": round(float(upper_color_conf), 6),
        "lower_color": _safe_color(lower_color) or Color.UNKNOWN,
        "lower_color_conf": round(float(lower_color_conf), 6),
    }


def _color_match_score(query_color: str | None, item_color: str | None) -> float:
    if not query_color:
        return 0.5
    if not item_color or item_color == Color.UNKNOWN:
        return 0.25
    if item_color == query_color:
        return 1.0
    neighbors = Color.NEIGHBORS.get(query_color, [query_color])
    if item_color in neighbors:
        return 0.65
    return 0.05


def _attribute_score(
    query: QueryVisualFeatures,
    *,
    query_upper_override: str | None,
    query_lower_override: str | None,
    item_upper: str | None,
    item_lower: str | None,
    raw: dict[str, Any],
) -> float:
    upper_color = query_upper_override or query.upper_color
    lower_color = query_lower_override or query.lower_color
    upper_weight = max(0.12, (1.0 if query_upper_override else query.upper_color_conf) * max(0.25, query.upper_visibility))
    lower_weight = max(0.12, (1.0 if query_lower_override else query.lower_color_conf) * max(0.25, query.lower_visibility))
    item_upper_weight = _safe_color_conf(raw.get("upper_color_conf"))
    item_lower_weight = _safe_color_conf(raw.get("lower_color_conf"))

    upper_score = _color_match_score(upper_color, item_upper)
    lower_score = _color_match_score(lower_color, item_lower)
    total_weight = (upper_weight * item_upper_weight) + (lower_weight * item_lower_weight)
    if total_weight <= 1e-12:
        return 0.5
    return max(
        0.0,
        min(
            1.0,
            (
                upper_score * upper_weight * item_upper_weight
                + lower_score * lower_weight * item_lower_weight
            )
            / total_weight,
        ),
    )


def _quality_component(item: dict[str, Any], raw: dict[str, Any]) -> float:
    quality_score = _to_float(item.get("quality_score"))
    if quality_score is None:
        quality_score = _to_float(raw.get("quality_score"))
    if quality_score is None:
        quality_score = 0.5
    area_ratio = _to_float(item.get("person_area_ratio"))
    if area_ratio is None:
        area_ratio = _to_float(raw.get("person_area_ratio"))
    if area_ratio is None:
        area_ratio = 0.03
    area_score = max(0.2, min(1.0, area_ratio / 0.08))
    return max(0.0, min(1.0, 0.72 * quality_score + 0.28 * area_score))


def _weighted_mean(components: list[tuple[float, float]]) -> float:
    total_weight = 0.0
    total_score = 0.0
    for score, weight in components:
        if weight <= 0.0:
            continue
        total_weight += weight
        total_score += max(0.0, min(1.0, score)) * weight
    if total_weight <= 1e-12:
        return 0.0
    return max(0.0, min(1.0, total_score / total_weight))


def _mode_compatibility_score(query_mode: str | None, candidate_mode: str | None) -> float:
    query_key = (query_mode or "unknown").strip().lower() or "unknown"
    candidate_key = (candidate_mode or "unknown").strip().lower() or "unknown"
    if query_key == "unknown" or candidate_key == "unknown":
        return 1.0
    if query_key == candidate_key:
        return 1.0
    if {query_key, candidate_key} == {"color", "low_light_color"}:
        return 0.95
    if {query_key, candidate_key} == {"low_light_color", "ir_bw"}:
        return 0.90
    if {query_key, candidate_key} == {"color", "ir_bw"}:
        return 0.82
    return 0.9


def _compute_final_score(
    query: QueryVisualFeatures,
    candidate: RankedCandidate,
    features: CandidateVisualFeatures,
    *,
    quality_score: float,
    upper_sim: float | None,
    lower_sim: float | None,
    face_sim: float | None,
    face_confidence: float,
) -> float:
    body_weight = 0.52
    if query.detection_used:
        body_weight += 0.08 * max(0.0, min(1.0, query.body_confidence))
        body_weight += 0.05 * max(0.0, min(1.0, query.person_area_ratio / 0.12))
    attr_weight = 0.06
    time_weight = 0.04
    quality_weight = 0.05
    upper_weight = 0.15 * max(0.1, min(query.upper_visibility, max(0.1, features.upper_visibility)))
    lower_weight = 0.11 * max(0.1, min(query.lower_visibility, max(0.1, features.lower_visibility)))
    face_weight = 0.22 * max(0.0, min(1.0, face_confidence)) if face_sim is not None else 0.0

    if query.image_mode == "ir_bw" or (candidate.image_mode or features.image_mode) == "ir_bw":
        attr_weight *= 0.25
    if query.quality_score < 0.35:
        attr_weight *= 0.7
        upper_weight *= 0.8
        lower_weight *= 0.8
        face_weight *= 0.8

    components: list[tuple[float, float]] = [
        (candidate.body_score, body_weight),
        (candidate.attr_score, attr_weight),
        (candidate.time_score, time_weight),
        (quality_score, quality_weight),
    ]
    if upper_sim is not None:
        components.append((upper_sim, upper_weight))
    if lower_sim is not None:
        components.append((lower_sim, lower_weight))
    if face_sim is not None and face_confidence > 0.0 and face_weight > 0.0:
        components.append((face_sim, face_weight))
    score = _weighted_mean(components)
    return max(
        0.0,
        min(
            1.0,
            score * _mode_compatibility_score(query.image_mode, candidate.image_mode or features.image_mode),
        ),
    )


def _to_track_id(item: dict[str, Any], fallback_idx: int) -> int:
    maybe_track = item.get("track_id")
    if isinstance(maybe_track, int):
        return max(1, maybe_track)
    key = str(item.get("image_path", "")) or str(item.get("image_name", ""))
    if not key:
        return fallback_idx + 1
    return max(1, zlib.crc32(key.encode("utf-8")))


def _recency_score(captured_at: datetime, now: datetime) -> float:
    seconds = max(0.0, (now - captured_at).total_seconds())
    hours = seconds / 3600.0
    return max(0.05, min(1.0, exp(-hours / 72.0)))


def _select_with_camera_diversity(
    ranked: list[tuple[float, SearchResultItem]],
    top_k: int,
) -> list[SearchResultItem]:
    if top_k <= 0:
        return []
    if not ranked:
        return []
    remaining = list(ranked)
    selected: list[SearchResultItem] = []
    camera_counts: dict[str, int] = {}
    while remaining and len(selected) < top_k:
        best_idx = 0
        best_adjusted = -1.0
        for idx, (score, item) in enumerate(remaining):
            camera = (item.camera_id or "").strip().lower() or "unknown"
            adjusted = score - 0.06 * camera_counts.get(camera, 0)
            if adjusted > best_adjusted:
                best_adjusted = adjusted
                best_idx = idx
        _, picked = remaining.pop(best_idx)
        camera = (picked.camera_id or "").strip().lower() or "unknown"
        camera_counts[camera] = camera_counts.get(camera, 0) + 1
        selected.append(picked)
    return selected


def _vector_literal(vec: list[float], dim: int = 512) -> str:
    values = [float(v) for v in vec[:dim]]
    if len(values) < dim:
        values.extend([0.0] * (dim - len(values)))
    return "[" + ",".join(f"{v:.6f}" for v in values) + "]"


def load_recent_metadata(limit: int = 5000) -> list[dict[str, Any]]:
    try:
        return capture_control_service.read_metadata_items(limit=max(1, min(limit, 20000)))
    except Exception:
        return []


@dataclass
class SearchBuildResult:
    items: list[SearchResultItem]
    timeline: dict[str, int]
    layer1_count: int
    layer2_count: int
    query_has_face: bool
    face_assist_used: bool
    reranked_count: int
    strategy: str
    filtered_count: int
    pre_rerank_count: int
    timings_ms: dict[str, int]


def _target_group_key(candidate: RankedCandidate) -> str:
    target_key = (candidate.target_key or "").strip()
    if target_key:
        return target_key
    return f"track_{candidate.track_id}"


def _group_aggregate_score(candidates: list[RankedCandidate]) -> float:
    if not candidates:
        return 0.0
    ordered = sorted(candidates, key=lambda item: item.final_score, reverse=True)
    top_scores = [item.final_score for item in ordered[:3]]
    best_score = top_scores[0]
    mean_top = sum(top_scores) / float(len(top_scores))
    return max(0.0, min(1.0, 0.72 * best_score + 0.28 * mean_top))


def _pick_group_representative(candidates: list[RankedCandidate]) -> RankedCandidate:
    if not candidates:
        raise ValueError("candidates cannot be empty")
    return max(
        candidates,
        key=lambda item: (
            item.final_score + 0.08 * float(item.quality_score or 0.0),
            item.body_score,
        ),
    )


def _candidate_to_evidence(candidate: RankedCandidate) -> SearchEvidenceItem:
    start_time = candidate.captured_at
    return SearchEvidenceItem(
        track_id=candidate.track_id,
        target_key=candidate.target_key,
        similarity=round(candidate.final_score, 6),
        body_sim=round(candidate.body_score, 6),
        upper_sim=round(candidate.upper_sim, 6) if candidate.upper_sim is not None else None,
        lower_sim=round(candidate.lower_sim, 6) if candidate.lower_sim is not None else None,
        face_sim=round(candidate.face_sim, 6) if candidate.face_sim is not None else None,
        attr_score=round(candidate.attr_score, 6),
        spacetime_score=round(candidate.time_score, 6),
        camera_id=candidate.camera_id,
        start_time=start_time,
        end_time=start_time + timedelta(seconds=2),
        upper_color=candidate.upper_color,
        lower_color=candidate.lower_color,
        image_path=candidate.image_path,
        person_bbox=_candidate_person_bbox(candidate),
        has_hat=candidate.has_hat,
        image_mode=candidate.image_mode,
        is_night=candidate.is_night,
        quality_score=round(float(candidate.quality_score), 4) if candidate.quality_score is not None else None,
        pose_hint=candidate.pose_hint,
        face_used=candidate.face_used,
        face_available=candidate.face_available,
    )


def _candidate_to_search_item(
    candidate: RankedCandidate,
    *,
    evidence: list[SearchEvidenceItem],
) -> SearchResultItem:
    start_time = candidate.captured_at
    return SearchResultItem(
        track_id=candidate.track_id,
        target_key=candidate.target_key,
        similarity=round(candidate.final_score, 6),
        body_sim=round(candidate.body_score, 6),
        upper_sim=round(candidate.upper_sim, 6) if candidate.upper_sim is not None else None,
        lower_sim=round(candidate.lower_sim, 6) if candidate.lower_sim is not None else None,
        face_sim=round(candidate.face_sim, 6) if candidate.face_sim is not None else None,
        attr_score=round(candidate.attr_score, 6),
        spacetime_score=round(candidate.time_score, 6),
        camera_id=candidate.camera_id,
        start_time=start_time,
        end_time=start_time + timedelta(seconds=2),
        upper_color=candidate.upper_color,
        lower_color=candidate.lower_color,
        image_path=candidate.image_path,
        person_bbox=_candidate_person_bbox(candidate),
        has_hat=candidate.has_hat,
        image_mode=candidate.image_mode,
        is_night=candidate.is_night,
        quality_score=round(float(candidate.quality_score), 4) if candidate.quality_score is not None else None,
        pose_hint=candidate.pose_hint,
        face_used=candidate.face_used,
        face_available=candidate.face_available,
        evidence_count=len(evidence),
        evidence=evidence,
    )


def _candidate_person_bbox(candidate: RankedCandidate) -> list[int] | None:
    source = candidate.source if isinstance(candidate.source, dict) else {}
    raw = _as_dict(source.get("raw")) or source
    bbox = _parse_bbox(source.get("person_bbox"))
    if bbox is None:
        bbox = _parse_bbox(raw.get("person_bbox"))
    if bbox is None:
        return None
    return [int(value) for value in bbox]


def build_search_results(
    *,
    image_bytes: bytes,
    top_k: int,
    upper_color: str | None,
    lower_color: str | None,
    time_start: datetime | None,
    time_end: datetime | None,
    has_hat: bool | None,
    camera_id: str | None,
    image_mode: str | None,
    is_night: bool | None,
    min_quality_score: float | None,
    pose_hint: str | None,
    face_mode: str,
    group_by_target: bool,
    diverse_camera: bool,
    now: datetime,
) -> SearchBuildResult:
    if now.tzinfo is None:
        now = ensure_aware(now)

    query = _extract_query_features(image_bytes)
    query_upper = _safe_color(upper_color) or query.upper_color
    query_lower = _safe_color(lower_color) or query.lower_color
    query_camera_id = (camera_id or "").strip().lower()
    query_image_mode = normalize_image_mode(image_mode)
    query_pose_hint = (pose_hint or "").strip().lower()
    effective_face_mode = face_mode if query.has_face else _FACE_MODE_OFF

    db_started = perf_counter()
    all_items: list[dict[str, Any]] = []
    layer1_count = 0
    filtered_count = 0
    db_query_ok = False
    strategy = "recent"
    try:
        layer1_count = capture_metadata_repo.count_records()
        query_vector = _vector_literal(query.body_embedding) if query.body_embedding else None
        filtered_count = capture_metadata_repo.count_search_candidates(
            camera_id=camera_id,
            has_hat=has_hat,
            image_mode=image_mode,
            is_night=is_night,
            pose_hint=pose_hint,
            min_quality_score=min_quality_score,
            time_start=time_start,
            time_end=time_end,
        )
        strategy = "exact"
        if query_vector and filtered_count > _SEARCH_EXACT_FILTER_THRESHOLD:
            strategy = "ann"
        candidate_limit = max(_SEARCH_DB_CANDIDATE_MIN, min(_SEARCH_DB_CANDIDATE_MAX, top_k * _SEARCH_DB_CANDIDATE_FACTOR))
        if strategy == "exact" and filtered_count > 0:
            candidate_limit = min(candidate_limit, filtered_count)
        all_items = capture_metadata_repo.search_candidates(
            query_vector=query_vector,
            limit=candidate_limit,
            camera_id=camera_id,
            has_hat=has_hat,
            image_mode=image_mode,
            is_night=is_night,
            pose_hint=pose_hint,
            min_quality_score=min_quality_score,
            time_start=time_start,
            time_end=time_end,
            strategy=strategy,
            ef_search=_SEARCH_ANN_EF_SEARCH,
        )
        db_query_ok = True
    except Exception:
        db_query_ok = False
    db_ms = int((perf_counter() - db_started) * 1000)

    if not db_query_ok:
        all_items = load_recent_metadata(limit=5000)
        layer1_count = len(all_items)
        filtered_count = len(all_items)
        strategy = "fallback_recent"

    preliminary_ranked: list[RankedCandidate] = []
    timeline: dict[str, int] = {}
    accepted_count = 0
    pre_rank_started = perf_counter()
    for idx, item in enumerate(all_items):
        captured_at = _parse_dt(item.get("captured_at")) or _parse_dt(item.get("event_time"))
        if captured_at is None:
            continue

        if time_start and captured_at < time_start:
            continue
        if time_end and captured_at > time_end:
            continue

        item_has_hat = _to_bool(item.get("has_hat"))
        if has_hat is not None and (item_has_hat is None or item_has_hat != has_hat):
            continue

        item_is_night = _to_bool(item.get("is_night"))
        if is_night is not None and (item_is_night is None or item_is_night != is_night):
            continue

        item_quality_score = _to_float(item.get("quality_score"))
        if min_quality_score is not None and (
            item_quality_score is None or item_quality_score < float(min_quality_score)
        ):
            continue

        item_camera_id = (
            str(item.get("camera_id", "")).strip()
            or str(item.get("camera_host", "")).strip()
            or "unknown"
        )
        if query_camera_id and item_camera_id.lower() != query_camera_id:
            continue

        item_pose_hint = str(item.get("pose_hint", "")).strip().lower()
        if query_pose_hint and item_pose_hint != query_pose_hint:
            continue

        raw = _as_dict(item.get("raw"))
        if not raw:
            raw = item
        item_image_mode = _item_image_mode(item, raw)
        if query_image_mode and item_image_mode != query_image_mode:
            continue
        item_upper = _safe_color(str(item.get("upper_color", "")).lower())
        item_lower = _safe_color(str(item.get("lower_color", "")).lower())
        body_score = _to_float(item.get("body_sim"))
        if body_score is None:
            body_score = _cosine_similarity(
                query.body_embedding,
                _to_float_list(item.get("body_embedding")),
            ) if query.body_embedding else 0.5
        attr_score = _attribute_score(
            query,
            query_upper_override=query_upper,
            query_lower_override=query_lower,
            item_upper=item_upper,
            item_lower=item_lower,
            raw=raw,
        )
        duplicate_score = _hash_similarity(
            query.image_hash,
            str(raw.get("image_hash_dhash", "")).strip() or None,
        )
        time_score = _recency_score(captured_at, now)
        quality_score = _quality_component(item, raw)
        preliminary_score = _weighted_mean(
            [
                (body_score, 0.70),
                (duplicate_score, 0.10),
                (attr_score, 0.10),
                (time_score, 0.06),
                (quality_score, 0.04),
            ]
        )

        accepted_count += 1
        bucket = f"{captured_at.hour:02d}"
        timeline[bucket] = timeline.get(bucket, 0) + 1
        preliminary_ranked.append(
            RankedCandidate(
                source=item,
                track_id=_to_track_id(item, idx),
                captured_at=captured_at,
                camera_id=item_camera_id,
                target_key=str(item.get("target_key", "")).strip() or None,
                image_path=str(item.get("image_path", "")).strip() or None,
                upper_color=item_upper or Color.UNKNOWN,
                lower_color=item_lower or Color.UNKNOWN,
                has_hat=item_has_hat,
                image_mode=item_image_mode,
                is_night=item_is_night,
                pose_hint=item_pose_hint or None,
                quality_score=item_quality_score,
                body_score=max(0.0, min(1.0, body_score)),
                attr_score=attr_score,
                time_score=time_score,
                preliminary_score=preliminary_score,
                final_score=preliminary_score,
            )
        )
    pre_rank_ms = int((perf_counter() - pre_rank_started) * 1000)

    preliminary_ranked.sort(key=lambda candidate: candidate.preliminary_score, reverse=True)
    pre_rerank_limit = max(_SEARCH_PRE_RERANK_MIN, top_k * _SEARCH_PRE_RERANK_FACTOR)
    pre_rerank_limit = max(top_k, min(_SEARCH_PRE_RERANK_MAX, pre_rerank_limit))
    pre_rerank_slice = preliminary_ranked[:pre_rerank_limit]
    rerank_limit = max(top_k * _SEARCH_RERANK_FACTOR, top_k)
    rerank_limit = max(12, min(_SEARCH_RERANK_MAX, rerank_limit))
    rerank_slice = pre_rerank_slice[:rerank_limit]
    asset_map: dict[int, dict[str, Any]] = {}
    asset_load_started = perf_counter()
    if db_query_ok and rerank_slice:
        try:
            asset_map = capture_metadata_repo.load_candidate_assets([item.track_id for item in rerank_slice])
        except Exception:
            asset_map = {}
    asset_load_ms = int((perf_counter() - asset_load_started) * 1000)

    face_assist_used = False
    rerank_started = perf_counter()
    for candidate in rerank_slice:
        merged = dict(candidate.source)
        asset = asset_map.get(candidate.track_id)
        if asset:
            merged.update(asset)
        features = _extract_candidate_features(
            merged,
            use_face=effective_face_mode == _FACE_MODE_ASSIST,
        )
        quality_score = _quality_component(merged, _as_dict(merged.get("raw")) or merged)
        if features.quality_score > 0.0:
            quality_score = max(0.0, min(1.0, 0.7 * quality_score + 0.3 * features.quality_score))
        upper_sim: float | None = None
        lower_sim: float | None = None
        face_sim: float | None = None
        face_confidence = 0.0

        if query.upper_embedding and features.upper_embedding:
            upper_sim = _cosine_similarity(query.upper_embedding, features.upper_embedding)
        if query.lower_embedding and features.lower_embedding:
            lower_sim = _cosine_similarity(query.lower_embedding, features.lower_embedding)
        if effective_face_mode == _FACE_MODE_ASSIST and query.has_face and features.has_face:
            face_sim = _cosine_similarity(query.face_embedding, features.face_embedding)
            face_confidence = min(query.face_confidence, features.face_confidence)
            face_assist_used = True

        candidate.upper_sim = upper_sim
        candidate.lower_sim = lower_sim
        candidate.face_sim = face_sim
        candidate.face_available = features.has_face
        candidate.face_used = face_sim is not None and face_confidence > 0.0
        if (candidate.image_mode or "unknown") == "unknown" and features.image_mode:
            candidate.image_mode = features.image_mode
        candidate.final_score = _compute_final_score(
            query,
            candidate,
            features,
            quality_score=quality_score,
            upper_sim=upper_sim,
            lower_sim=lower_sim,
            face_sim=face_sim,
            face_confidence=face_confidence,
        )
    rerank_ms = int((perf_counter() - rerank_started) * 1000)

    group_started = perf_counter()
    final_ranked = sorted(
        preliminary_ranked,
        key=lambda candidate: candidate.final_score,
        reverse=True,
    )
    grouped_candidates: dict[str, list[RankedCandidate]] = {}
    for candidate in final_ranked:
        grouped_candidates.setdefault(_target_group_key(candidate), []).append(candidate)

    if group_by_target:
        base_results: list[tuple[float, SearchResultItem]] = []
        group_ranked = sorted(
            grouped_candidates.items(),
            key=lambda entry: _group_aggregate_score(entry[1]),
            reverse=True,
        )
        for _, members in group_ranked[: max(top_k * 3, top_k)]:
            representative = _pick_group_representative(members)
            evidence = [_candidate_to_evidence(item) for item in members[:5]]
            base_results.append(
                (
                    _group_aggregate_score(members),
                    _candidate_to_search_item(representative, evidence=evidence),
                )
            )
    else:
        base_results = [
            (
                candidate.final_score,
                _candidate_to_search_item(candidate, evidence=[_candidate_to_evidence(candidate)]),
            )
            for candidate in final_ranked
        ]

    if diverse_camera:
        results = _select_with_camera_diversity(base_results, top_k)
    else:
        results = [item for _, item in base_results[:top_k]]
    group_ms = int((perf_counter() - group_started) * 1000)

    return SearchBuildResult(
        items=results,
        timeline=dict(sorted(timeline.items(), key=lambda pair: pair[0])),
        layer1_count=layer1_count,
        layer2_count=accepted_count,
        query_has_face=query.has_face,
        face_assist_used=face_assist_used,
        reranked_count=min(len(preliminary_ranked), rerank_limit),
        strategy=strategy,
        filtered_count=filtered_count,
        pre_rerank_count=min(len(preliminary_ranked), pre_rerank_limit),
        timings_ms={
            "db_ms": db_ms,
            "pre_rank_ms": pre_rank_ms,
            "asset_load_ms": asset_load_ms,
            "heavy_rerank_ms": rerank_ms,
            "group_ms": group_ms,
        },
    )
