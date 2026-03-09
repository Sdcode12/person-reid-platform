from __future__ import annotations

from typing import Final

import cv2
import numpy as np

IMAGE_MODE_COLOR: Final[str] = "color"
IMAGE_MODE_IR_BW: Final[str] = "ir_bw"
IMAGE_MODE_LOW_LIGHT_COLOR: Final[str] = "low_light_color"
IMAGE_MODE_UNKNOWN: Final[str] = "unknown"
IMAGE_MODE_ALL: Final[tuple[str, ...]] = (
    IMAGE_MODE_COLOR,
    IMAGE_MODE_IR_BW,
    IMAGE_MODE_LOW_LIGHT_COLOR,
    IMAGE_MODE_UNKNOWN,
)


def normalize_image_mode(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    value = raw.strip().lower()
    if value in IMAGE_MODE_ALL:
        return value
    return None


def infer_image_mode(image: np.ndarray, low_light_threshold: float = 70.0) -> str:
    if image is None or image.size == 0:
        return IMAGE_MODE_UNKNOWN

    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    sat = hsv[:, :, 1].astype(np.float32)
    val = hsv[:, :, 2].astype(np.float32)
    sat_mean = float(sat.mean())
    val_mean = float(val.mean())
    color_ratio = float((sat >= 24.0).sum()) / max(1.0, float(sat.size))

    bgr = image.astype(np.float32)
    channel_delta = (
        np.abs(bgr[:, :, 0] - bgr[:, :, 1])
        + np.abs(bgr[:, :, 1] - bgr[:, :, 2])
        + np.abs(bgr[:, :, 0] - bgr[:, :, 2])
    ) / 3.0
    channel_delta_mean = float(channel_delta.mean())

    if color_ratio <= 0.04 and sat_mean < 14.0 and channel_delta_mean < 6.0:
        return IMAGE_MODE_IR_BW
    if val_mean < low_light_threshold:
        return IMAGE_MODE_LOW_LIGHT_COLOR
    return IMAGE_MODE_COLOR
