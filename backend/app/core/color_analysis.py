from __future__ import annotations

import cv2
import numpy as np

from app.core.constants import Color


def _focus_crop(image: np.ndarray) -> np.ndarray:
    if image is None or image.size == 0:
        return image
    height, width = image.shape[:2]
    if height < 8 or width < 8:
        return image
    x1 = int(round(width * 0.18))
    x2 = int(round(width * 0.82))
    y1 = int(round(height * 0.08))
    y2 = int(round(height * 0.92))
    if x2 <= x1 or y2 <= y1:
        return image
    return image[y1:y2, x1:x2]


def _normalize_for_chroma(image: np.ndarray) -> np.ndarray:
    if image is None or image.size == 0:
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
        gain = 128.0 / current_v
        gain = max(0.75, min(1.45, gain))
        hsv[:, :, 2] = np.clip(hsv[:, :, 2] * gain, 0, 255)
    return cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)


def _safe_mean(mask: np.ndarray) -> float:
    if mask.size == 0:
        return 0.0
    return float(mask.mean())


def dominant_color(image: np.ndarray) -> tuple[str, float]:
    if image is None or image.size == 0:
        return Color.UNKNOWN, 0.0

    raw = _focus_crop(image)
    chroma_ref = _normalize_for_chroma(raw)

    hsv_raw = cv2.cvtColor(raw, cv2.COLOR_BGR2HSV)
    hsv_ref = cv2.cvtColor(chroma_ref, cv2.COLOR_BGR2HSV)
    lab_raw = cv2.cvtColor(raw, cv2.COLOR_BGR2LAB)

    h = hsv_ref[:, :, 0]
    s = hsv_ref[:, :, 1]
    v = hsv_raw[:, :, 2]
    l = lab_raw[:, :, 0]

    achromatic_mask = s < 52
    achromatic_ratio = _safe_mean(achromatic_mask.astype(np.float32))
    dark_ratio = _safe_mean(((l < 78) | (v < 58)).astype(np.float32))
    bright_ratio = _safe_mean(((l > 190) | (v > 188)).astype(np.float32))
    medium_ratio = _safe_mean(((l >= 78) & (l <= 185) & achromatic_mask).astype(np.float32))

    achromatic_l = l[achromatic_mask] if achromatic_ratio >= 0.25 else l.reshape(-1)
    achromatic_v = v[achromatic_mask] if achromatic_ratio >= 0.25 else v.reshape(-1)
    median_l = float(np.median(achromatic_l)) if achromatic_l.size else float(np.median(l))
    median_v = float(np.median(achromatic_v)) if achromatic_v.size else float(np.median(v))

    chroma_mask = (s >= 52) & (v >= 40)
    chroma_total = int(max(1, chroma_mask.sum()))
    chroma_counts = {
        Color.RED: int((((h <= 10) | (h >= 170)) & chroma_mask).sum()),
        Color.ORANGE: int(((h >= 11) & (h <= 24) & chroma_mask).sum()),
        Color.YELLOW: int(((h >= 25) & (h <= 34) & chroma_mask).sum()),
        Color.GREEN: int(((h >= 35) & (h <= 85) & chroma_mask).sum()),
        Color.BLUE: int(((h >= 86) & (h <= 130) & chroma_mask).sum()),
        Color.PURPLE: int(((h >= 131) & (h <= 169) & chroma_mask).sum()),
        Color.BROWN: int(((h >= 10) & (h <= 25) & (s >= 78) & (v >= 25) & (v <= 168)).sum()),
    }
    top_chroma = max(chroma_counts, key=chroma_counts.get)
    top_chroma_ratio = chroma_counts[top_chroma] / float(chroma_total)
    chroma_share = chroma_counts[top_chroma] / float(max(1, raw.shape[0] * raw.shape[1]))

    if top_chroma_ratio >= 0.42 and chroma_share >= 0.12:
        conf = max(0.0, min(1.0, 0.55 * top_chroma_ratio + 0.45 * chroma_share))
        return top_chroma, float(conf)

    if achromatic_ratio >= 0.48:
        if median_l < 62 or median_v < 52 or (median_l < 70 and dark_ratio >= 0.78):
            conf = max(0.0, min(1.0, 0.55 * achromatic_ratio + 0.45 * dark_ratio))
            return Color.BLACK, float(conf)
        if median_l > 188 or (median_l > 176 and bright_ratio >= 0.40) or median_v > 188:
            conf = max(0.0, min(1.0, 0.55 * achromatic_ratio + 0.45 * bright_ratio))
            return Color.WHITE, float(conf)
        conf = max(0.0, min(1.0, 0.50 * achromatic_ratio + 0.50 * medium_ratio))
        return Color.GRAY, float(conf)

    if top_chroma_ratio >= 0.26 and chroma_share >= 0.08:
        conf = max(0.0, min(1.0, 0.55 * top_chroma_ratio + 0.45 * chroma_share))
        return top_chroma, float(conf)

    if median_l < 70 and achromatic_ratio >= 0.34 and dark_ratio >= 0.78:
        conf = max(0.0, min(1.0, 0.45 * achromatic_ratio + 0.55 * dark_ratio))
        return Color.BLACK, float(conf)
    if median_l > 182 and achromatic_ratio >= 0.30:
        conf = max(0.0, min(1.0, 0.45 * achromatic_ratio + 0.55 * bright_ratio))
        return Color.WHITE, float(conf)
    if achromatic_ratio >= 0.30:
        conf = max(0.0, min(1.0, 0.45 * achromatic_ratio + 0.55 * medium_ratio))
        return Color.GRAY, float(conf)

    return Color.UNKNOWN, float(max(chroma_share, achromatic_ratio * 0.5))
