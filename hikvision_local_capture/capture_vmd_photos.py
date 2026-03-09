from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo
from zoneinfo import ZoneInfoNotFoundError

import cv2
import httpx
import numpy as np
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.constants import Color
from app.core.color_analysis import dominant_color
from app.core.detector import Detection, PersonDetector, ReIDEmbeddingExtractor
from app.core.image_mode import IMAGE_MODE_LOW_LIGHT_COLOR, infer_image_mode
try:
    from app.services.capture_metadata_repo import capture_metadata_repo
except Exception:  # noqa: BLE001
    capture_metadata_repo = None  # type: ignore[assignment]

HIK_NS = "{http://www.hikvision.com/ver20/XMLSchema}"
_CAPTURE_TZ_NAME = os.getenv("CAPTURE_TIMEZONE", "Asia/Shanghai").strip() or "Asia/Shanghai"
try:
    CAPTURE_TZ = ZoneInfo(_CAPTURE_TZ_NAME)
except ZoneInfoNotFoundError:
    CAPTURE_TZ = timezone.utc


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
class CameraConfig:
    name: str
    host: str
    port: int
    username: str
    password: str
    channel_id: int
    scheme: str = "http"
    stream_path: str = "/ISAPI/Event/notification/alertStream"
    picture_path_template: str = "/ISAPI/Streaming/channels/{channel_no}/picture"
    stream_url_override: str = ""
    picture_url_override: str = ""
    request_timeout_seconds: float = 10.0
    snapshot_retry_count: int = 2
    snapshot_retry_delay_ms: int = 120
    cooldown_seconds: float = 2.0
    burst_count: int = 7
    max_capture_attempts: int = 28
    burst_interval_ms: int = 250
    detector_mode: str = "auto"
    yolo_model_path: str = "models/yolov8n.onnx"
    yolo_input_size: int = 640
    confidence_threshold: float = 0.35
    nms_threshold: float = 0.45
    reid_mode: str = "auto"
    reid_model_path: str = "backend/models/person_reid.onnx"
    reid_input_width: int = 128
    reid_input_height: int = 256
    hog_hit_threshold: float = 0.0
    min_person_confidence: float = 0.2
    min_person_area_ratio: float = 0.01
    enable_tiled_detection: bool = True
    tiled_detection_grid_size: int = 2
    tiled_detection_overlap_ratio: float = 0.25
    tiled_detection_trigger_area_ratio: float = 0.03
    enable_bbox_stabilization: bool = True
    bbox_stabilize_alpha: float = 0.72
    bbox_stabilize_iou_threshold: float = 0.18
    bbox_stabilize_center_distance_ratio: float = 0.22
    enable_rider_rescue: bool = True
    rider_rescue_confidence: float = 0.1
    rider_rescue_area_ratio: float = 0.002
    min_laplacian_var: float = 70.0
    min_brightness: float = 35.0
    max_brightness: float = 230.0
    min_contrast_std: float = 18.0
    dedup_hamming_threshold: int = 8
    min_consecutive_vmd_active: int = 2
    active_window_seconds: float = 3.0
    same_target_suppress_seconds: float = 90.0
    same_target_embedding_similarity: float = 0.94
    same_target_hash_hamming_threshold: int = 4
    same_target_area_ratio_delta: float = 0.08
    enable_color_normalization: bool = True
    color_target_brightness: float = 128.0
    night_brightness_threshold: float = 70.0
    save_sidecar_json: bool = True
    save_metadata_jsonl: bool = True
    metadata_jsonl_name: str = "metadata.jsonl"
    save_to_db: bool = True
    save_local_image: bool = False
    local_fallback_on_db_error: bool = True
    verbose_events: bool = False

    @property
    def base_url(self) -> str:
        return f"{self.scheme}://{self.host}:{self.port}"

    @property
    def channel_no(self) -> int:
        return self.channel_id * 100 + 1

    @property
    def stream_url(self) -> str:
        if self.stream_url_override.strip():
            return self.stream_url_override.format(channel_no=self.channel_no)
        return f"{self.base_url}{self.stream_path}"

    @property
    def picture_url(self) -> str:
        if self.picture_url_override.strip():
            return self.picture_url_override.format(channel_no=self.channel_no)
        return f"{self.base_url}{self.picture_path_template.format(channel_no=self.channel_no)}"


def _first_text(root: ET.Element, tag: str) -> str:
    node = root.find(f"{HIK_NS}{tag}")
    if node is None:
        node = root.find(tag)
    if node is None or node.text is None:
        return ""
    return node.text.strip()


def parse_event_xml(xml_text: str) -> dict[str, Any] | None:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None

    event_type = _first_text(root, "eventType")
    event_state = _first_text(root, "eventState")
    if not event_type:
        return None

    return {
        "event_type": event_type,
        "event_state": event_state,
        "event_time": _first_text(root, "dateTime"),
        "ip_address": _first_text(root, "ipAddress"),
        "channel_id": _first_text(root, "channelID"),
        "description": _first_text(root, "eventDescription"),
    }


def _load_config_from_raw(raw: dict[str, Any], base_path: Path) -> tuple[CameraConfig, Path]:
    camera = raw.get("camera", {})
    output = raw.get("output", {})
    detector = raw.get("detector", {})
    quality = raw.get("quality", {})
    dedup = raw.get("dedup", {})
    reliability = raw.get("reliability", {})
    color = raw.get("color", {})
    logging_cfg = raw.get("logging", {})

    cfg = CameraConfig(
        name=str(camera.get("name", "hikvision_camera")),
        host=str(camera["host"]),
        port=int(camera.get("port", 80)),
        username=str(camera["username"]),
        password=str(camera["password"]),
        channel_id=int(camera.get("channel_id", 1)),
        scheme=str(camera.get("scheme", "http")),
        stream_path=str(camera.get("stream_path", "/ISAPI/Event/notification/alertStream")),
        picture_path_template=str(
            camera.get("picture_path_template", "/ISAPI/Streaming/channels/{channel_no}/picture")
        ),
        stream_url_override=str(camera.get("stream_url_override", "")),
        picture_url_override=str(camera.get("picture_url_override", "")),
        request_timeout_seconds=float(camera.get("request_timeout_seconds", 10.0)),
        snapshot_retry_count=max(0, int(camera.get("snapshot_retry_count", 2))),
        snapshot_retry_delay_ms=max(0, int(camera.get("snapshot_retry_delay_ms", 120))),
        cooldown_seconds=float(camera.get("cooldown_seconds", 2.0)),
        burst_count=max(1, int(camera.get("burst_count", 7))),
        max_capture_attempts=max(1, int(camera.get("max_capture_attempts", 28))),
        burst_interval_ms=max(0, int(camera.get("burst_interval_ms", 250))),
        detector_mode=str(detector.get("mode", "auto")),
        yolo_model_path=str(detector.get("yolo_model_path", "models/yolov8n.onnx")),
        yolo_input_size=int(detector.get("yolo_input_size", 640)),
        confidence_threshold=float(detector.get("confidence_threshold", 0.35)),
        nms_threshold=float(detector.get("nms_threshold", 0.45)),
        reid_mode=str(detector.get("reid_mode", "auto")),
        reid_model_path=str(detector.get("reid_model_path", "backend/models/person_reid.onnx")),
        reid_input_width=int(detector.get("reid_input_width", 128)),
        reid_input_height=int(detector.get("reid_input_height", 256)),
        hog_hit_threshold=float(detector.get("hog_hit_threshold", 0.0)),
        min_person_confidence=float(detector.get("min_person_confidence", 0.2)),
        min_person_area_ratio=float(detector.get("min_person_area_ratio", 0.01)),
        enable_tiled_detection=bool(detector.get("enable_tiled_detection", True)),
        tiled_detection_grid_size=max(1, int(detector.get("tiled_detection_grid_size", 2))),
        tiled_detection_overlap_ratio=float(detector.get("tiled_detection_overlap_ratio", 0.25)),
        tiled_detection_trigger_area_ratio=float(detector.get("tiled_detection_trigger_area_ratio", 0.03)),
        enable_bbox_stabilization=bool(detector.get("enable_bbox_stabilization", True)),
        bbox_stabilize_alpha=float(detector.get("bbox_stabilize_alpha", 0.72)),
        bbox_stabilize_iou_threshold=float(detector.get("bbox_stabilize_iou_threshold", 0.18)),
        bbox_stabilize_center_distance_ratio=float(
            detector.get("bbox_stabilize_center_distance_ratio", 0.22)
        ),
        enable_rider_rescue=bool(detector.get("enable_rider_rescue", True)),
        rider_rescue_confidence=float(detector.get("rider_rescue_confidence", 0.1)),
        rider_rescue_area_ratio=float(detector.get("rider_rescue_area_ratio", 0.002)),
        min_laplacian_var=float(quality.get("min_laplacian_var", 70.0)),
        min_brightness=float(quality.get("min_brightness", 35.0)),
        max_brightness=float(quality.get("max_brightness", 230.0)),
        min_contrast_std=float(quality.get("min_contrast_std", 18.0)),
        dedup_hamming_threshold=max(0, int(dedup.get("hamming_threshold", 8))),
        min_consecutive_vmd_active=max(1, int(reliability.get("min_consecutive_vmd_active", 2))),
        active_window_seconds=max(0.1, float(reliability.get("active_window_seconds", 3.0))),
        same_target_suppress_seconds=max(1.0, float(reliability.get("same_target_suppress_seconds", 90.0))),
        same_target_embedding_similarity=float(
            reliability.get("same_target_embedding_similarity", 0.94)
        ),
        same_target_hash_hamming_threshold=max(
            0, int(reliability.get("same_target_hash_hamming_threshold", 4))
        ),
        same_target_area_ratio_delta=max(0.0, float(reliability.get("same_target_area_ratio_delta", 0.08))),
        enable_color_normalization=bool(color.get("enable_normalization", True)),
        color_target_brightness=float(color.get("target_brightness", 128.0)),
        night_brightness_threshold=float(color.get("night_brightness_threshold", 70.0)),
        save_sidecar_json=bool(output.get("save_sidecar_json", True)),
        save_metadata_jsonl=bool(output.get("save_metadata_jsonl", True)),
        metadata_jsonl_name=str(output.get("metadata_jsonl_name", "metadata.jsonl")),
        save_to_db=bool(output.get("save_to_db", True)),
        save_local_image=bool(output.get("save_local_image", False)),
        local_fallback_on_db_error=bool(output.get("local_fallback_on_db_error", True)),
        verbose_events=bool(logging_cfg.get("verbose_events", False)),
    )
    output_dir = Path(str(output.get("dir", "photos")))
    if not output_dir.is_absolute():
        output_dir = base_path.parent / output_dir
    return cfg, output_dir


def load_config(path: Path) -> tuple[CameraConfig, Path]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return _load_config_from_raw(raw, path)


def load_config_stdin(base_path: Path) -> tuple[CameraConfig, Path]:
    raw_text = sys.stdin.read()
    if not raw_text.strip():
        raise ValueError("config stdin is empty")
    raw = yaml.safe_load(raw_text) or {}
    if not isinstance(raw, dict):
        raise ValueError("config stdin must be a yaml mapping")
    return _load_config_from_raw(raw, base_path)


def _safe_token(text: str) -> str:
    token = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in text.strip().lower())
    return token or "unknown"


def _debug(cfg: CameraConfig, message: str) -> None:
    if cfg.verbose_events:
        print(message)


def _crop(frame: np.ndarray, bbox: tuple[int, int, int, int]) -> np.ndarray:
    x, y, w, h = bbox
    frame_h, frame_w = frame.shape[:2]
    x1 = max(0, min(x, frame_w - 1))
    y1 = max(0, min(y, frame_h - 1))
    x2 = max(x1 + 1, min(x + w, frame_w))
    y2 = max(y1 + 1, min(y + h, frame_h))
    return frame[y1:y2, x1:x2]


def _bbox_area(bbox: tuple[int, int, int, int]) -> int:
    return max(1, int(bbox[2]) * int(bbox[3]))


def _bbox_area_ratio(bbox: tuple[int, int, int, int], frame_area: int) -> float:
    return _bbox_area(bbox) / float(max(1, frame_area))


def _bbox_aspect_ratio(bbox: tuple[int, int, int, int]) -> float:
    return float(int(bbox[3]) / max(1, int(bbox[2])))


def _bbox_iou(box_a: tuple[int, int, int, int], box_b: tuple[int, int, int, int]) -> float:
    ax1, ay1, aw, ah = box_a
    bx1, by1, bw, bh = box_b
    ax2 = ax1 + aw
    ay2 = ay1 + ah
    bx2 = bx1 + bw
    by2 = by1 + bh
    inter_x1 = max(ax1, bx1)
    inter_y1 = max(ay1, by1)
    inter_x2 = min(ax2, bx2)
    inter_y2 = min(ay2, by2)
    inter_w = max(0, inter_x2 - inter_x1)
    inter_h = max(0, inter_y2 - inter_y1)
    inter = inter_w * inter_h
    if inter <= 0:
        return 0.0
    union = _bbox_area(box_a) + _bbox_area(box_b) - inter
    if union <= 0:
        return 0.0
    return float(inter / union)


def _bbox_center_distance_ratio(
    box_a: tuple[int, int, int, int],
    box_b: tuple[int, int, int, int],
    frame_shape: tuple[int, int, int] | tuple[int, int],
) -> float:
    frame_h, frame_w = int(frame_shape[0]), int(frame_shape[1])
    ax = float(box_a[0] + box_a[2] / 2.0)
    ay = float(box_a[1] + box_a[3] / 2.0)
    bx = float(box_b[0] + box_b[2] / 2.0)
    by = float(box_b[1] + box_b[3] / 2.0)
    dx = ax - bx
    dy = ay - by
    diag = max(1.0, (frame_w**2 + frame_h**2) ** 0.5)
    return float((dx * dx + dy * dy) ** 0.5 / diag)


def _detection_priority(det: Detection, frame_area: int) -> float:
    return float(det.confidence) + min(0.4, _bbox_area_ratio(det.bbox, frame_area) * 4.0)


def _dedupe_detections(
    detections: list[Detection],
    *,
    frame_area: int,
    iou_threshold: float = 0.5,
) -> list[Detection]:
    if not detections:
        return []
    ordered = sorted(detections, key=lambda det: _detection_priority(det, frame_area), reverse=True)
    kept: list[Detection] = []
    for det in ordered:
        if any(_bbox_iou(det.bbox, prev.bbox) >= iou_threshold for prev in kept):
            continue
        kept.append(det)
    return kept


def _tile_axis_starts(length: int, tile_length: int, grid_size: int) -> list[int]:
    if length <= tile_length:
        return [0]
    if grid_size <= 1:
        return [0, max(0, length - tile_length)]
    max_start = max(0, length - tile_length)
    raw = np.linspace(0, max_start, num=grid_size, dtype=np.float32)
    starts = sorted({int(round(float(item))) for item in raw.tolist()} | {0, max_start})
    return starts


def _tile_windows(frame: np.ndarray, cfg: CameraConfig) -> list[tuple[int, int, int, int]]:
    if not cfg.enable_tiled_detection or frame is None or frame.size == 0:
        return []
    frame_h, frame_w = frame.shape[:2]
    if frame_w < 1280 and frame_h < 720:
        return []
    grid_size = max(2, int(cfg.tiled_detection_grid_size))
    overlap = max(0.0, min(0.45, float(cfg.tiled_detection_overlap_ratio)))
    tile_w = min(frame_w, max(640, int(round((frame_w / float(grid_size)) * (1.0 + overlap)))))
    tile_h = min(frame_h, max(640, int(round((frame_h / float(grid_size)) * (1.0 + overlap)))))
    xs = _tile_axis_starts(frame_w, tile_w, grid_size)
    ys = _tile_axis_starts(frame_h, tile_h, grid_size)
    out: list[tuple[int, int, int, int]] = []
    seen: set[tuple[int, int, int, int]] = set()
    for y in ys:
        for x in xs:
            window = (int(x), int(y), int(tile_w), int(tile_h))
            if window in seen:
                continue
            seen.add(window)
            out.append(window)
    return out


def _run_tiled_detection(
    frame: np.ndarray,
    detector: PersonDetector,
    cfg: CameraConfig,
) -> list[Detection]:
    detections: list[Detection] = []
    for tile_x, tile_y, tile_w, tile_h in _tile_windows(frame, cfg):
        tile = frame[tile_y : tile_y + tile_h, tile_x : tile_x + tile_w]
        if tile.size == 0:
            continue
        for det in detector.detect(tile):
            x, y, w, h = det.bbox
            detections.append(
                Detection(
                    bbox=(tile_x + int(x), tile_y + int(y), int(w), int(h)),
                    confidence=float(det.confidence),
                )
            )
    return detections


def _normalize_for_color(image: np.ndarray, cfg: CameraConfig) -> np.ndarray:
    if image.size == 0 or not cfg.enable_color_normalization:
        return image

    bgr = image.astype(np.float32)
    means = bgr.reshape(-1, 3).mean(axis=0)
    gray_mean = float(means.mean())
    scales = gray_mean / (means + 1e-6)
    bgr *= scales.reshape(1, 1, 3)
    bgr = np.clip(bgr, 0, 255).astype(np.uint8)

    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV).astype(np.float32)
    current_v = float(hsv[:, :, 2].mean())
    if current_v > 1e-6:
        gain = cfg.color_target_brightness / current_v
        gain = max(0.6, min(1.8, gain))
        hsv[:, :, 2] = np.clip(hsv[:, :, 2] * gain, 0, 255)
    return cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)


def _quality_metrics(image: np.ndarray) -> dict[str, float]:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    return {
        "lap_var": float(cv2.Laplacian(gray, cv2.CV_64F).var()),
        "brightness": float(gray.mean()),
        "contrast_std": float(gray.std()),
    }


def _is_quality_ok(metrics: dict[str, float], cfg: CameraConfig) -> bool:
    return (
        metrics["lap_var"] >= cfg.min_laplacian_var
        and cfg.min_brightness <= metrics["brightness"] <= cfg.max_brightness
        and metrics["contrast_std"] >= cfg.min_contrast_std
    )


def _quality_score(metrics: dict[str, float], cfg: CameraConfig) -> float:
    lap_scale = max(cfg.min_laplacian_var, 1.0)
    contrast_scale = max(cfg.min_contrast_std, 1.0)
    target_brightness = (cfg.min_brightness + cfg.max_brightness) / 2.0
    brightness_half_range = max((cfg.max_brightness - cfg.min_brightness) / 2.0, 1.0)

    sharp_score = min(1.0, metrics["lap_var"] / (lap_scale * 1.5))
    contrast_score = min(1.0, metrics["contrast_std"] / (contrast_scale * 1.5))
    brightness_score = max(
        0.0, 1.0 - abs(metrics["brightness"] - target_brightness) / brightness_half_range
    )

    score = 0.45 * sharp_score + 0.30 * brightness_score + 0.25 * contrast_score
    return max(0.0, min(1.0, score))


def _dhash(image: np.ndarray, hash_size: int = 8) -> int:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    resized = cv2.resize(gray, (hash_size + 1, hash_size), interpolation=cv2.INTER_AREA)
    diff = resized[:, 1:] > resized[:, :-1]
    value = 0
    for bit in diff.flatten():
        value = (value << 1) | int(bool(bit))
    return value


def _is_duplicate(hash_value: int, hashes: list[int], threshold: int) -> bool:
    for existed in hashes:
        if (hash_value ^ existed).bit_count() <= threshold:
            return True
    return False


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
    flat = np.asarray(raw, dtype=np.float32).reshape(-1)
    denom = float(np.linalg.norm(flat))
    if denom > 1e-12:
        flat = flat / denom
    return [round(float(v), 6) for v in flat.tolist()]


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
    return signature, round(confidence, 6)


def _extract_region_embedding(image: np.ndarray, reid_extractor: ReIDEmbeddingExtractor) -> list[float]:
    if image.size == 0 or min(image.shape[:2]) < 24:
        return []
    return reid_extractor.extract(image)


def _pose_hint(det: Detection) -> str:
    _, _, w, h = det.bbox
    if h <= 0:
        return "unknown"
    ratio = w / float(h)
    if ratio < 0.42:
        return "side"
    if ratio <= 0.78:
        return "front_or_back"
    return "partial_or_close"


def _build_target_key(
    *,
    camera_name: str,
    camera_host: str,
    channel: str,
    features: dict[str, Any],
) -> str:
    embedding = features.get("body_embedding", [])
    quantized: list[str] = []
    if isinstance(embedding, list):
        for value in embedding[:24]:
            try:
                v = float(value)
            except Exception:
                v = 0.0
            quantized.append(str(int(round(v * 20.0))))
    payload = "|".join(
        [
            _safe_token(camera_name),
            _safe_token(camera_host),
            _safe_token(channel),
            _safe_token(str(features.get("upper_color", "unknown"))),
            _safe_token(str(features.get("lower_color", "unknown"))),
            _safe_token(str(features.get("head_color", "unknown"))),
            _safe_token(str(features.get("has_hat", "unknown"))),
            _safe_token(str(features.get("pose_hint", "unknown"))),
            ",".join(quantized),
        ]
    )
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]
    return f"tk_{digest}"


def _extract_features(
    frame: np.ndarray,
    det: Detection,
    cfg: CameraConfig,
    reid_extractor: ReIDEmbeddingExtractor,
) -> dict[str, Any]:
    person_crop_raw = _crop(frame, det.bbox)
    if person_crop_raw.size == 0:
        return {
            "upper_color": Color.UNKNOWN,
            "upper_color_conf": 0.0,
            "lower_color": Color.UNKNOWN,
            "lower_color_conf": 0.0,
            "head_color": Color.UNKNOWN,
            "head_color_conf": 0.0,
            "has_hat": "unknown",
            "has_hat_conf": 0.0,
            "pose_hint": "unknown",
            "body_embedding": [],
            "upper_embedding": [],
            "lower_embedding": [],
            "face_embedding": [],
            "face_confidence": 0.0,
        }

    person_crop = _normalize_for_color(person_crop_raw, cfg)

    h = person_crop.shape[0]
    raw_h = person_crop_raw.shape[0]
    upper = person_crop[: max(1, int(h * 0.55)), :]
    lower = person_crop[max(0, int(h * 0.55)) :, :]
    raw_upper = person_crop_raw[: max(1, int(raw_h * 0.55)), :]
    raw_lower = person_crop_raw[max(0, int(raw_h * 0.55)) :, :]
    raw_head = person_crop_raw[: max(1, int(raw_h * 0.22)), :]

    upper_color, upper_color_conf = dominant_color(raw_upper)
    lower_color, lower_color_conf = dominant_color(raw_lower)
    head_color, head_color_conf = dominant_color(raw_head)

    hsv_head = cv2.cvtColor(raw_head, cv2.COLOR_BGR2HSV)
    dark_ratio = float((hsv_head[:, :, 2] < 60).sum()) / max(1.0, float(raw_head.shape[0] * raw_head.shape[1]))
    has_hat = "yes" if dark_ratio > 0.55 else "no"
    has_hat_conf = min(1.0, abs(dark_ratio - 0.55) / 0.45)
    body_embed = reid_extractor.extract(person_crop)
    upper_embed = _extract_region_embedding(upper, reid_extractor)
    lower_embed = _extract_region_embedding(lower, reid_extractor)
    face_embed, face_confidence = _extract_face_features(person_crop_raw)

    return {
        "upper_color": upper_color,
        "upper_color_conf": round(upper_color_conf, 4),
        "lower_color": lower_color,
        "lower_color_conf": round(lower_color_conf, 4),
        "head_color": head_color,
        "head_color_conf": round(head_color_conf, 4),
        "has_hat": has_hat,
        "has_hat_conf": round(has_hat_conf, 4),
        "pose_hint": _pose_hint(det),
        "body_embedding": body_embed,
        "upper_embedding": upper_embed,
        "lower_embedding": lower_embed,
        "face_embedding": face_embed,
        "face_confidence": face_confidence,
    }


def _decode_jpeg(jpeg_bytes: bytes) -> np.ndarray | None:
    buf = np.frombuffer(jpeg_bytes, dtype=np.uint8)
    if buf.size == 0:
        return None
    return cv2.imdecode(buf, cv2.IMREAD_COLOR)


def _fetch_snapshot_frame(
    client: httpx.Client,
    cfg: CameraConfig,
    shot_index: int,
) -> tuple[np.ndarray | None, bytes | None, str | None, str]:
    attempts = max(1, int(cfg.snapshot_retry_count) + 1)
    last_reason = "request_failed"
    last_detail = ""
    for attempt in range(1, attempts + 1):
        try:
            resp = client.get(cfg.picture_url, timeout=cfg.request_timeout_seconds)
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "").lower()
            if "image" not in content_type:
                last_reason = "non_image"
                last_detail = content_type
            else:
                frame = _decode_jpeg(resp.content)
                if frame is not None:
                    return frame, resp.content, content_type, "ok"
                last_reason = "decode_failed"
                last_detail = "decode jpg failed"
        except Exception as exc:
            last_reason = "request_failed"
            last_detail = str(exc)
        if attempt < attempts:
            _debug(
                cfg,
                f"[snapshot-retry] shot={shot_index} attempt={attempt}/{attempts} "
                f"reason={last_reason} detail={last_detail}",
            )
            if cfg.snapshot_retry_delay_ms > 0:
                time.sleep(cfg.snapshot_retry_delay_ms / 1000.0)
    if last_reason == "request_failed":
        print(f"[snapshot] request failed: {last_detail}")
    elif last_reason == "non_image":
        print(f"[snapshot] unexpected content-type={last_detail}")
    elif last_reason == "decode_failed":
        print("[snapshot] decode jpg failed")
    return None, None, None, last_reason


def _build_detector(cfg: CameraConfig) -> PersonDetector:
    # Use a lower internal candidate threshold than the final acceptance threshold.
    # Otherwise 0.2-0.35 confidence people never reach the later filtering stage.
    candidate_confidence = min(float(cfg.confidence_threshold), _relaxed_person_confidence(cfg))
    return PersonDetector(
        mode=cfg.detector_mode,
        hit_threshold=cfg.hog_hit_threshold,
        yolo_model_path=cfg.yolo_model_path,
        yolo_input_size=cfg.yolo_input_size,
        confidence_threshold=candidate_confidence,
        nms_threshold=cfg.nms_threshold,
        yolo_person_class_ids=[0],
    )


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(payload, ensure_ascii=False)
    with path.open("a", encoding="utf-8") as f:
        f.write(line)
        f.write("\n")


def _cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0
    a = np.asarray(vec_a, dtype=np.float32)
    b = np.asarray(vec_b, dtype=np.float32)
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom <= 1e-12:
        return 0.0
    return float(np.dot(a, b) / denom)


def _prune_recent_targets(recent_targets: list[dict[str, Any]], now_ts: float, ttl: float) -> None:
    keep: list[dict[str, Any]] = []
    for item in recent_targets:
        ts = float(item.get("ts", 0.0))
        if now_ts - ts <= ttl:
            keep.append(item)
    recent_targets[:] = keep


def _is_recent_same_target(
    *,
    recent_targets: list[dict[str, Any]],
    cfg: CameraConfig,
    now_ts: float,
    dedup_group_id: str,
    channel: str,
    img_hash: int,
    body_embedding: list[float],
    person_area_ratio: float,
) -> bool:
    _prune_recent_targets(recent_targets, now_ts, cfg.same_target_suppress_seconds)
    for item in recent_targets:
        if str(item.get("dedup_group_id", "")) == dedup_group_id:
            continue
        if str(item.get("channel", "")) != channel:
            continue

        hamming = (img_hash ^ int(item.get("hash", 0))).bit_count()
        if hamming <= cfg.same_target_hash_hamming_threshold:
            return True

        sim = _cosine_similarity(body_embedding, item.get("embedding", []))
        area_delta = abs(person_area_ratio - float(item.get("area_ratio", 0.0)))
        if sim >= cfg.same_target_embedding_similarity and area_delta <= cfg.same_target_area_ratio_delta:
            return True
    return False


def _relaxed_person_confidence(cfg: CameraConfig) -> float:
    return max(0.08, min(float(cfg.min_person_confidence), float(cfg.min_person_confidence) * 0.6))


def _relaxed_person_area_ratio(cfg: CameraConfig) -> float:
    return max(0.0025, min(float(cfg.min_person_area_ratio), float(cfg.min_person_area_ratio) * 0.35))


def _filter_person_detections(
    detections: list[Detection],
    *,
    frame_area: int,
    min_confidence: float,
    min_area_ratio: float,
) -> list[Detection]:
    filtered: list[Detection] = []
    total_area = float(max(1, frame_area))
    for det in detections:
        if det.confidence < min_confidence:
            continue
        area = max(1, det.bbox[2] * det.bbox[3])
        if area / total_area < min_area_ratio:
            continue
        filtered.append(det)
    return filtered


def _filter_rider_detections(
    detections: list[Detection],
    *,
    frame_area: int,
    cfg: CameraConfig,
) -> list[Detection]:
    if not cfg.enable_rider_rescue:
        return []
    rider_conf = max(0.08, float(cfg.rider_rescue_confidence))
    rider_area = max(0.0015, float(cfg.rider_rescue_area_ratio))
    out: list[Detection] = []
    for det in detections:
        area_ratio = _bbox_area_ratio(det.bbox, frame_area)
        aspect_ratio = _bbox_aspect_ratio(det.bbox)
        if det.confidence < rider_conf:
            continue
        if area_ratio < rider_area:
            continue
        if 0.8 <= aspect_ratio <= 2.35:
            out.append(det)
    return out


def _select_main_detection(
    detections: list[Detection],
    *,
    frame_area: int,
    frame_shape: tuple[int, int, int] | tuple[int, int],
    event_context: dict[str, Any] | None,
) -> Detection:
    if len(detections) == 1:
        return detections[0]
    prev_bbox = None
    if isinstance(event_context, dict):
        raw_bbox = event_context.get("last_bbox")
        if isinstance(raw_bbox, (list, tuple)) and len(raw_bbox) == 4:
            try:
                prev_bbox = tuple(int(v) for v in raw_bbox)
            except Exception:
                prev_bbox = None
    scored: list[tuple[float, Detection]] = []
    for det in detections:
        area_ratio = _bbox_area_ratio(det.bbox, frame_area)
        score = float(det.confidence) * 0.55 + min(0.3, area_ratio * 4.0)
        if prev_bbox is not None:
            score += _bbox_iou(det.bbox, prev_bbox) * 0.3
            score += max(0.0, 0.15 - (_bbox_center_distance_ratio(det.bbox, prev_bbox, frame_shape) * 0.5))
        aspect_ratio = _bbox_aspect_ratio(det.bbox)
        if 0.9 <= aspect_ratio <= 2.4:
            score += 0.03
        scored.append((score, det))
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]


def _stabilize_detection(
    det: Detection,
    *,
    frame_shape: tuple[int, int, int] | tuple[int, int],
    cfg: CameraConfig,
    event_context: dict[str, Any] | None,
) -> Detection:
    if not cfg.enable_bbox_stabilization or not isinstance(event_context, dict):
        return det
    raw_bbox = event_context.get("last_bbox")
    if not isinstance(raw_bbox, (list, tuple)) or len(raw_bbox) != 4:
        return det
    try:
        prev_bbox = tuple(int(v) for v in raw_bbox)
    except Exception:
        return det
    iou = _bbox_iou(det.bbox, prev_bbox)
    center_ratio = _bbox_center_distance_ratio(det.bbox, prev_bbox, frame_shape)
    if iou < float(cfg.bbox_stabilize_iou_threshold) and center_ratio > float(cfg.bbox_stabilize_center_distance_ratio):
        return det
    alpha = max(0.0, min(1.0, float(cfg.bbox_stabilize_alpha)))
    smoothed = []
    for prev_v, cur_v in zip(prev_bbox, det.bbox):
        smoothed.append(int(round((prev_v * (1.0 - alpha)) + (cur_v * alpha))))
    return Detection(bbox=(smoothed[0], smoothed[1], max(1, smoothed[2]), max(1, smoothed[3])), confidence=det.confidence)


def _detect_people_for_snapshot(
    frame: np.ndarray,
    detector: PersonDetector,
    cfg: CameraConfig,
    shot_index: int,
) -> tuple[list[Detection], str]:
    frame_area = max(1, frame.shape[0] * frame.shape[1])
    base_detections = detector.detect(frame)
    detections = list(base_detections)
    max_area_ratio = max((_bbox_area_ratio(det.bbox, frame_area) for det in base_detections), default=0.0)
    used_tiled = False
    if cfg.enable_tiled_detection and (
        not base_detections or max_area_ratio <= float(cfg.tiled_detection_trigger_area_ratio)
    ):
        tiled = _run_tiled_detection(frame, detector, cfg)
        if tiled:
            used_tiled = True
            detections.extend(tiled)
            detections = _dedupe_detections(detections, frame_area=frame_area, iou_threshold=0.48)

    filtered = _filter_person_detections(
        detections,
        frame_area=frame_area,
        min_confidence=float(cfg.min_person_confidence),
        min_area_ratio=float(cfg.min_person_area_ratio),
    )
    if filtered:
        return filtered, "tiled_strict" if used_tiled else "strict"

    relaxed_confidence = _relaxed_person_confidence(cfg)
    relaxed_area_ratio = _relaxed_person_area_ratio(cfg)
    filtered = _filter_person_detections(
        detections,
        frame_area=frame_area,
        min_confidence=relaxed_confidence,
        min_area_ratio=relaxed_area_ratio,
    )
    if filtered:
        _debug(
            cfg,
            f"[rescue] shot={shot_index} relaxed_person conf>={relaxed_confidence:.2f} "
            f"area>={relaxed_area_ratio:.4f} hits={len(filtered)} method={'tiled' if used_tiled else 'global'}",
        )
        return filtered, "tiled_relaxed" if used_tiled else "relaxed"

    rider_filtered = _filter_rider_detections(detections, frame_area=frame_area, cfg=cfg)
    if rider_filtered:
        _debug(
            cfg,
            f"[rescue] shot={shot_index} rider_person conf>={cfg.rider_rescue_confidence:.2f} "
            f"area>={cfg.rider_rescue_area_ratio:.4f} hits={len(rider_filtered)} method={'tiled' if used_tiled else 'global'}",
        )
        return rider_filtered, "tiled_rider" if used_tiled else "rider"

    return [], "none"


def save_snapshot_if_person(
    client: httpx.Client,
    detector: PersonDetector,
    reid_extractor: ReIDEmbeddingExtractor,
    cfg: CameraConfig,
    output_dir: Path,
    event: dict[str, Any],
    shot_index: int,
    saved_hashes: list[int],
    dedup_group_id: str,
    metadata_jsonl_path: Path | None,
    recent_targets: list[dict[str, Any]],
    event_context: dict[str, Any],
) -> tuple[str | None, int, dict[str, Any] | None, str]:
    frame, image_bytes, content_type, snapshot_reason = _fetch_snapshot_frame(client, cfg, shot_index)
    if frame is None or image_bytes is None or content_type is None:
        return None, 0, None, snapshot_reason

    frame_area = max(1, frame.shape[0] * frame.shape[1])
    filtered, detection_source = _detect_people_for_snapshot(
        frame,
        detector,
        cfg,
        shot_index,
    )

    if not filtered:
        _debug(cfg, f"[skip] shot={shot_index} no_person")
        return None, 0, None, "no_person"

    filtered = _dedupe_detections(filtered, frame_area=frame_area, iou_threshold=0.45)
    main_det = _select_main_detection(
        filtered,
        frame_area=frame_area,
        frame_shape=frame.shape,
        event_context=event_context,
    )
    main_det = _stabilize_detection(
        main_det,
        frame_shape=frame.shape,
        cfg=cfg,
        event_context=event_context,
    )
    event_context["last_bbox"] = [int(v) for v in main_det.bbox]
    event_context["last_confidence"] = float(main_det.confidence)
    event_context["last_detection_source"] = detection_source
    person_crop = _crop(frame, main_det.bbox)
    if person_crop.size == 0:
        _debug(cfg, f"[skip] shot={shot_index} invalid_crop")
        return None, 0, None, "invalid_crop"

    metrics = _quality_metrics(person_crop)
    if not _is_quality_ok(metrics, cfg):
        _debug(
            cfg,
            "[skip] shot="
            f"{shot_index} low_quality lap_var={metrics['lap_var']:.1f} "
            f"brightness={metrics['brightness']:.1f} contrast={metrics['contrast_std']:.1f}",
        )
        return None, 0, None, "low_quality"

    norm_crop = _normalize_for_color(person_crop, cfg)
    img_hash = _dhash(norm_crop)
    if _is_duplicate(img_hash, saved_hashes, cfg.dedup_hamming_threshold):
        _debug(cfg, f"[skip] shot={shot_index} duplicate")
        return None, 0, None, "duplicate"

    features = _extract_features(frame, main_det, cfg, reid_extractor)
    quality_score = _quality_score(metrics, cfg)

    now = datetime.now(CAPTURE_TZ)
    day = now.strftime("%Y-%m-%d")
    ts = now.strftime("%Y%m%d_%H%M%S_%f")
    channel = event.get("channel_id") or str(cfg.channel_id)
    camera_dir = _safe_token(f"{cfg.name}_{cfg.host}_ch{channel}")
    x, y, w, h = main_det.bbox
    person_area_ratio = max(1, w * h) / float(frame_area)
    now_ts = time.time()
    if _is_recent_same_target(
        recent_targets=recent_targets,
        cfg=cfg,
        now_ts=now_ts,
        dedup_group_id=dedup_group_id,
        channel=str(channel),
        img_hash=img_hash,
        body_embedding=features["body_embedding"],
        person_area_ratio=person_area_ratio,
    ):
        _debug(cfg, f"[skip] shot={shot_index} same_target_recent")
        return None, 0, None, "same_target_recent"

    file_name = (
        f"{ts}"
        f"_s{shot_index:02d}"
        f"_p{len(filtered)}"
        f"_u{_safe_token(str(features['upper_color']))}"
        f"_l{_safe_token(str(features['lower_color']))}"
        f"_h{_safe_token(str(features['head_color']))}"
        f"_hat{_safe_token(str(features['has_hat']))}.jpg"
    )
    db_image_path = f"db://{camera_dir}/{day}/{file_name}"
    save_path: Path | None = None
    persisted_local = False
    persisted_db = False
    should_save_local = bool(cfg.save_local_image)

    light_level = float(metrics["brightness"])
    image_mode = infer_image_mode(frame, low_light_threshold=cfg.night_brightness_threshold)
    metadata: dict[str, Any] = {
        "camera_id": cfg.name,
        "camera_host": cfg.host,
        "channel_id": str(channel),
        "event_type": str(event.get("event_type", "")),
        "event_state": str(event.get("event_state", "")),
        "event_time": str(event.get("event_time", "")),
        "captured_at": now.isoformat(timespec="milliseconds"),
        "dedup_group_id": dedup_group_id,
        "shot_index": int(shot_index),
        "people_count": int(len(filtered)),
        "image_path": db_image_path,
        "image_name": file_name,
        "image_width": int(frame.shape[1]),
        "image_height": int(frame.shape[0]),
        "person_bbox": [int(x), int(y), int(w), int(h)],
        "person_confidence": round(float(main_det.confidence), 4),
        "person_area_ratio": round(person_area_ratio, 6),
        "detection_source": detection_source,
        "quality_metrics": {
            "lap_var": round(float(metrics["lap_var"]), 4),
            "brightness": round(float(metrics["brightness"]), 4),
            "contrast_std": round(float(metrics["contrast_std"]), 4),
        },
        "quality_score": round(float(quality_score), 4),
        "light_level": round(light_level, 4),
        "image_mode": image_mode,
        "is_night": bool(image_mode == IMAGE_MODE_LOW_LIGHT_COLOR),
        "upper_color": features["upper_color"],
        "upper_color_conf": features["upper_color_conf"],
        "lower_color": features["lower_color"],
        "lower_color_conf": features["lower_color_conf"],
        "head_color": features["head_color"],
        "head_color_conf": features["head_color_conf"],
        "has_hat": features["has_hat"],
        "has_hat_conf": features["has_hat_conf"],
        "pose_hint": features["pose_hint"],
        "body_embedding": features["body_embedding"],
        "upper_embedding": features["upper_embedding"],
        "lower_embedding": features["lower_embedding"],
        "face_embedding": features["face_embedding"],
        "face_confidence": features["face_confidence"],
        "image_hash_dhash": f"{img_hash:016x}",
    }
    metadata["target_key"] = _build_target_key(
        camera_name=cfg.name,
        camera_host=cfg.host,
        channel=str(channel),
        features=features,
    )

    def _write_local_files() -> None:
        nonlocal save_path, persisted_local
        if persisted_local:
            return
        save_dir = output_dir / day / camera_dir
        save_dir.mkdir(parents=True, exist_ok=True)
        save_path = save_dir / file_name
        save_path.write_bytes(image_bytes)
        metadata["image_path"] = str(save_path)
        metadata["image_name"] = save_path.name
        if cfg.save_sidecar_json:
            sidecar_path = save_path.with_suffix(".json")
            metadata["sidecar_path"] = str(sidecar_path)
            sidecar_path.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
        if cfg.save_metadata_jsonl and metadata_jsonl_path is not None:
            _append_jsonl(metadata_jsonl_path, metadata)
        persisted_local = True

    if should_save_local:
        _write_local_files()

    if cfg.save_to_db and capture_metadata_repo is not None:
        try:
            capture_metadata_repo.upsert_item(
                item=metadata,
                image_bytes=image_bytes,
                image_mime_type=content_type,
                image_size=len(image_bytes),
            )
            persisted_db = True
        except Exception as exc:
            print(f"[db] upsert failed shot={shot_index}: {exc}")

    if not persisted_db and not persisted_local and cfg.local_fallback_on_db_error:
        _write_local_files()

    if not persisted_db and not persisted_local:
        return None, 0, None, "db_write_failed"

    saved_hashes.append(img_hash)
    recent_targets.append(
        {
            "ts": now_ts,
            "dedup_group_id": dedup_group_id,
            "channel": str(channel),
            "hash": int(img_hash),
            "embedding": features["body_embedding"],
            "area_ratio": float(person_area_ratio),
        }
    )
    persisted_path = str(metadata.get("image_path", "")).strip() or db_image_path
    reason = "saved_db" if persisted_db else "saved_local"
    return persisted_path, len(filtered), features, reason


def run(cfg: CameraConfig, output_dir: Path) -> int:
    if cfg.save_local_image or cfg.save_metadata_jsonl:
        output_dir.mkdir(parents=True, exist_ok=True)
    metadata_jsonl_path = output_dir / cfg.metadata_jsonl_name if (cfg.save_metadata_jsonl and cfg.save_local_image) else None
    stop_flag = {"stop": False}
    last_save_time = 0.0
    runtime_state: dict[str, Any] = {
        "last_vmd_active_ts": 0.0,
        "consecutive_vmd_active": 0.0,
        "recent_targets": [],
    }
    detector = _build_detector(cfg)
    reid_extractor = ReIDEmbeddingExtractor(
        mode=cfg.reid_mode,
        model_path=cfg.reid_model_path,
        input_width=cfg.reid_input_width,
        input_height=cfg.reid_input_height,
        output_dim=512,
    )

    def _stop_handler(_: int, __: Any) -> None:
        stop_flag["stop"] = True

    signal.signal(signal.SIGINT, _stop_handler)
    signal.signal(signal.SIGTERM, _stop_handler)

    auth: httpx.Auth | None = None
    if cfg.username.strip() and cfg.password.strip():
        auth = httpx.DigestAuth(cfg.username, cfg.password)
    timeout = httpx.Timeout(
        connect=cfg.request_timeout_seconds,
        read=None,
        write=cfg.request_timeout_seconds,
        pool=cfg.request_timeout_seconds,
    )
    print(f"[start] stream={cfg.stream_url}")
    print(f"[start] picture={cfg.picture_url}")
    print(f"[start] output={output_dir}")
    relaxed_confidence = _relaxed_person_confidence(cfg)
    relaxed_area_ratio = _relaxed_person_area_ratio(cfg)
    print(
        "[start] detector="
        f"{cfg.detector_mode} model={cfg.yolo_model_path} "
        f"candidate_conf={min(float(cfg.confidence_threshold), relaxed_confidence):.2f} "
        f"min_conf={cfg.min_person_confidence} min_area_ratio={cfg.min_person_area_ratio} "
        f"rescue_conf={relaxed_confidence:.2f} rescue_area_ratio={relaxed_area_ratio:.4f}"
    )
    print(
        "[start] rescue="
        f"snapshot_retries={cfg.snapshot_retry_count} retry_delay_ms={cfg.snapshot_retry_delay_ms} "
        f"tiled_detection={cfg.enable_tiled_detection} grid={cfg.tiled_detection_grid_size} "
        f"bbox_stabilization={cfg.enable_bbox_stabilization} rider_rescue={cfg.enable_rider_rescue}"
    )
    print(
        "[start] reid="
        f"mode={cfg.reid_mode} "
        f"model={cfg.reid_model_path} "
        f"input={cfg.reid_input_width}x{cfg.reid_input_height} "
        f"backend={reid_extractor.backend_name}"
    )
    print(
        "[start] capture="
        f"target_saved={cfg.burst_count} max_capture_attempts={cfg.max_capture_attempts} "
        f"burst_interval_ms={cfg.burst_interval_ms} "
        f"cooldown_seconds={cfg.cooldown_seconds}"
    )
    print(
        "[start] reliability="
        f"min_consecutive_vmd_active={cfg.min_consecutive_vmd_active} "
        f"active_window_seconds={cfg.active_window_seconds} "
        f"same_target_suppress_seconds={cfg.same_target_suppress_seconds}"
    )
    print(
        "[start] anti_repeat="
        f"embedding_sim>={cfg.same_target_embedding_similarity} "
        f"hash_hamming<={cfg.same_target_hash_hamming_threshold} "
        f"area_ratio_delta<={cfg.same_target_area_ratio_delta}"
    )
    print(
        "[start] quality="
        f"lap_var>={cfg.min_laplacian_var} "
        f"brightness=[{cfg.min_brightness},{cfg.max_brightness}] "
        f"contrast_std>={cfg.min_contrast_std} "
        f"dedup_hamming<={cfg.dedup_hamming_threshold}"
    )
    print(
        "[start] metadata="
        f"sidecar_json={cfg.save_sidecar_json} "
        f"jsonl={cfg.save_metadata_jsonl} "
        f"jsonl_path={metadata_jsonl_path if metadata_jsonl_path is not None else 'disabled'} "
        f"save_to_db={cfg.save_to_db and capture_metadata_repo is not None} "
        f"save_local_image={cfg.save_local_image} "
        f"local_fallback_on_db_error={cfg.local_fallback_on_db_error} "
        f"night_brightness<{cfg.night_brightness_threshold}"
    )
    if cfg.save_to_db and capture_metadata_repo is None:
        print("[warn] save_to_db=true but DB writer is unavailable; capture will fallback to local behavior only")

    while not stop_flag["stop"]:
        try:
            with httpx.Client(auth=auth, timeout=timeout, follow_redirects=True) as client:
                with client.stream("GET", cfg.stream_url) as resp:
                    resp.raise_for_status()
                    collecting = False
                    lines: list[str] = []

                    for raw_line in resp.iter_lines():
                        if stop_flag["stop"]:
                            break
                        if raw_line is None:
                            continue

                        line = raw_line.strip()
                        if "<EventNotificationAlert" in line:
                            collecting = True
                            lines = [line]
                            if "</EventNotificationAlert>" in line:
                                collecting = False
                                event = parse_event_xml("\n".join(lines))
                                if event:
                                    last_save_time = handle_event(
                                        client=client,
                                        detector=detector,
                                        reid_extractor=reid_extractor,
                                        cfg=cfg,
                                        output_dir=output_dir,
                                        event=event,
                                        last_save_time=last_save_time,
                                        runtime_state=runtime_state,
                                        metadata_jsonl_path=metadata_jsonl_path,
                                    )
                            continue

                        if collecting:
                            lines.append(line)
                            if "</EventNotificationAlert>" in line:
                                collecting = False
                                event = parse_event_xml("\n".join(lines))
                                if event:
                                    last_save_time = handle_event(
                                        client=client,
                                        detector=detector,
                                        reid_extractor=reid_extractor,
                                        cfg=cfg,
                                        output_dir=output_dir,
                                        event=event,
                                        last_save_time=last_save_time,
                                        runtime_state=runtime_state,
                                        metadata_jsonl_path=metadata_jsonl_path,
                                    )
        except Exception as exc:
            if stop_flag["stop"]:
                break
            print(f"[stream] disconnected: {exc}")
            time.sleep(2.0)

    print("[stop] exited")
    return 0


def handle_event(
    client: httpx.Client,
    detector: PersonDetector,
    reid_extractor: ReIDEmbeddingExtractor,
    cfg: CameraConfig,
    output_dir: Path,
    event: dict[str, Any],
    last_save_time: float,
    runtime_state: dict[str, Any],
    metadata_jsonl_path: Path | None,
) -> float:
    event_type = str(event.get("event_type", "")).upper()
    event_state = str(event.get("event_state", "")).lower()
    if cfg.verbose_events:
        print(
            f"[event] type={event_type} state={event_state} "
            f"channel={event.get('channel_id', '')} time={event.get('event_time', '')}"
        )

    if event_type != "VMD":
        return last_save_time

    if event_state != "active":
        if event_state == "inactive":
            runtime_state["consecutive_vmd_active"] = 0.0
        return last_save_time

    now_ts = time.time()
    prev_active_ts = float(runtime_state.get("last_vmd_active_ts", 0.0))
    if now_ts - prev_active_ts <= cfg.active_window_seconds:
        runtime_state["consecutive_vmd_active"] = float(runtime_state.get("consecutive_vmd_active", 0.0)) + 1.0
    else:
        runtime_state["consecutive_vmd_active"] = 1.0
    runtime_state["last_vmd_active_ts"] = now_ts

    consecutive = int(runtime_state["consecutive_vmd_active"])
    if consecutive < cfg.min_consecutive_vmd_active:
        _debug(cfg, f"[gate] consecutive_vmd_active={consecutive}/{cfg.min_consecutive_vmd_active}")
        return last_save_time

    if now_ts - last_save_time < cfg.cooldown_seconds:
        _debug(cfg, "[gate] cooldown")
        return last_save_time

    saved_count = 0
    saved_hashes: list[int] = []
    recent_targets = runtime_state.setdefault("recent_targets", [])
    reason_counts: dict[str, int] = {}
    event_context: dict[str, Any] = {}
    dedup_group_id = f"{datetime.now(CAPTURE_TZ).strftime('%Y%m%d%H%M%S')}_{uuid4().hex[:8]}"
    max_attempts = max(cfg.burst_count, cfg.max_capture_attempts)
    for i in range(max_attempts):
        saved, people_count, features, reason = save_snapshot_if_person(
            client=client,
            detector=detector,
            reid_extractor=reid_extractor,
            cfg=cfg,
            output_dir=output_dir,
            event=event,
            shot_index=i + 1,
            saved_hashes=saved_hashes,
            dedup_group_id=dedup_group_id,
            metadata_jsonl_path=metadata_jsonl_path,
            recent_targets=recent_targets,
            event_context=event_context,
        )
        reason_counts[reason] = reason_counts.get(reason, 0) + 1
        if saved is not None:
            saved_count += 1
            print(
                f"[person] time={event.get('event_time', '')} shot={i + 1}/{max_attempts} "
                f"saved={saved_count}/{cfg.burst_count} people={people_count} "
                f"upper={features.get('upper_color') if features else 'unknown'} "
                f"lower={features.get('lower_color') if features else 'unknown'} "
                f"head={features.get('head_color') if features else 'unknown'} "
                f"hat={features.get('has_hat') if features else 'unknown'} "
                f"pose={features.get('pose_hint') if features else 'unknown'} "
                f"path={saved}"
            )
            if saved_count >= cfg.burst_count:
                break
        if i < max_attempts - 1 and cfg.burst_interval_ms > 0:
            time.sleep(cfg.burst_interval_ms / 1000.0)

    if saved_count < cfg.burst_count:
        reasons = " ".join(
            f"{key}:{val}"
            for key, val in sorted(reason_counts.items(), key=lambda kv: (-kv[1], kv[0]))
            if not key.startswith("saved")
        )
        print(
            f"[event-summary] time={event.get('event_time', '')} saved={saved_count}/{cfg.burst_count} "
            f"attempts={max_attempts} reasons={reasons or 'none'}"
        )

    if saved_count > 0:
        return now_ts
    return last_save_time


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture Hikvision VMD-triggered photos to database/local storage.")
    parser.add_argument("--config", default="hikvision_local_capture/config.yaml", help="Path to yaml config, or '-' to read config from stdin.")
    parser.add_argument(
        "--config-base",
        default="",
        help="Base path used to resolve relative paths when --config=-.",
    )
    args = parser.parse_args()

    if str(args.config).strip() == "-":
        base_path = Path(args.config_base).expanduser() if str(args.config_base).strip() else (Path.cwd() / "capture_runtime.stdin.yaml")
        if not base_path.is_absolute():
            base_path = base_path.resolve()
        try:
            cfg, output_dir = load_config_stdin(base_path)
        except Exception as exc:
            print(f"failed to read config from stdin: {exc}")
            return 2
    else:
        cfg_path = Path(args.config).resolve()
        if not cfg_path.exists():
            print(f"config not found: {cfg_path}")
            return 2
        cfg, output_dir = load_config(cfg_path)
    return run(cfg, output_dir)


if __name__ == "__main__":
    sys.exit(main())
