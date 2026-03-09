from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any, Iterable

import cv2
import numpy as np

from app.core.logging import get_logger


@dataclass
class Detection:
    bbox: tuple[int, int, int, int]
    confidence: float


class _HogPersonDetector:
    def __init__(self, hit_threshold: float = 0.0) -> None:
        self._hog = cv2.HOGDescriptor()
        self._hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
        self._hit_threshold = hit_threshold

    def detect(self, frame: np.ndarray) -> list[Detection]:
        if frame is None or frame.size == 0:
            return []

        rects, weights = self._hog.detectMultiScale(
            frame,
            winStride=(8, 8),
            padding=(8, 8),
            scale=1.03,
            hitThreshold=self._hit_threshold,
        )

        detections: list[Detection] = []
        for (x, y, w, h), weight in zip(rects, weights):
            if w <= 0 or h <= 0:
                continue
            detections.append(
                Detection(
                    bbox=(int(x), int(y), int(w), int(h)),
                    confidence=float(weight),
                )
            )

        detections.sort(key=lambda d: d.confidence, reverse=True)
        return detections


class _YoloOnnxPersonDetector:
    def __init__(
        self,
        model_path: str,
        input_size: int,
        confidence_threshold: float,
        nms_threshold: float,
        person_class_ids: Iterable[int],
    ) -> None:
        self._logger = get_logger("detector.yolo")
        self._input_size = max(64, int(input_size))
        self._confidence_threshold = float(confidence_threshold)
        self._nms_threshold = float(nms_threshold)
        self._person_class_ids = tuple(sorted({int(cid) for cid in person_class_ids}))
        if not self._person_class_ids:
            self._person_class_ids = (0,)

        self._net: Any | None = None
        self._ready = False

        resolved = self._resolve_model_path(model_path)
        if resolved is None:
            self._logger.warning(
                "YOLO model file not found at %s; detector will fallback to HOG",
                model_path,
            )
            return

        try:
            self._net = cv2.dnn.readNetFromONNX(str(resolved))
            self._ready = True
            self._logger.info("YOLO ONNX detector enabled model=%s", resolved)
        except Exception as exc:
            self._logger.warning(
                "failed to load YOLO ONNX model=%s error=%s; detector will fallback to HOG",
                resolved,
                exc,
            )

    @property
    def is_ready(self) -> bool:
        return self._ready

    def detect(self, frame: np.ndarray) -> list[Detection]:
        if not self._ready or self._net is None:
            return []
        if frame is None or frame.size == 0:
            return []

        try:
            blob = cv2.dnn.blobFromImage(
                frame,
                scalefactor=1.0 / 255.0,
                size=(self._input_size, self._input_size),
                swapRB=True,
                crop=False,
            )
            self._net.setInput(blob)
            raw_output = self._net.forward()
        except Exception as exc:
            self._ready = False
            self._logger.warning(
                "YOLO forward failed (%s); disabling YOLO detector for this process",
                exc,
            )
            return []

        frame_h, frame_w = frame.shape[:2]
        return self._postprocess(raw_output=raw_output, frame_w=frame_w, frame_h=frame_h)

    def _resolve_model_path(self, model_path: str) -> Path | None:
        path = Path(model_path)
        candidates = [path]
        if not path.is_absolute():
            backend_root = Path(__file__).resolve().parents[2]
            repo_root = backend_root.parent
            candidates = [
                Path.cwd() / path,
                backend_root / path,
                repo_root / path,
            ]

        for candidate in candidates:
            if candidate.exists():
                return candidate
        return None

    def _postprocess(self, raw_output: Any, frame_w: int, frame_h: int) -> list[Detection]:
        predictions = np.asarray(raw_output)
        if predictions.size == 0:
            return []

        if predictions.ndim == 3 and predictions.shape[0] == 1:
            predictions = predictions[0]
        if predictions.ndim != 2:
            return []

        if predictions.shape[0] in {84, 85} and predictions.shape[1] > predictions.shape[0]:
            predictions = predictions.T
        elif predictions.shape[1] < 6 and predictions.shape[0] >= 6:
            predictions = predictions.T

        if predictions.ndim != 2 or predictions.shape[1] < 5:
            return []

        boxes: list[list[int]] = []
        scores: list[float] = []
        input_size = float(self._input_size)

        for row in predictions:
            col_count = int(row.shape[0])
            if col_count >= 85:
                objectness = float(row[4])
                class_scores = row[5:]
            else:
                objectness = 1.0
                class_scores = row[4:]

            best_conf = 0.0
            for class_id in self._person_class_ids:
                if class_id < 0 or class_id >= class_scores.shape[0]:
                    continue
                score = float(class_scores[class_id]) * objectness
                if score > best_conf:
                    best_conf = score

            if best_conf < self._confidence_threshold:
                continue

            cx, cy, bw, bh = (float(row[0]), float(row[1]), float(row[2]), float(row[3]))
            if max(abs(cx), abs(cy), abs(bw), abs(bh)) <= 2.0:
                cx *= frame_w
                cy *= frame_h
                bw *= frame_w
                bh *= frame_h
            else:
                cx *= frame_w / input_size
                cy *= frame_h / input_size
                bw *= frame_w / input_size
                bh *= frame_h / input_size

            x = int(round(cx - bw / 2.0))
            y = int(round(cy - bh / 2.0))
            w = int(round(bw))
            h = int(round(bh))
            if w <= 1 or h <= 1:
                continue

            x = max(0, min(x, frame_w - 1))
            y = max(0, min(y, frame_h - 1))
            w = max(1, min(w, frame_w - x))
            h = max(1, min(h, frame_h - y))

            boxes.append([x, y, w, h])
            scores.append(best_conf)

        if not boxes:
            return []

        raw_indices = cv2.dnn.NMSBoxes(
            bboxes=boxes,
            scores=scores,
            score_threshold=self._confidence_threshold,
            nms_threshold=self._nms_threshold,
        )

        if raw_indices is None or len(raw_indices) == 0:
            return []

        indices: list[int] = []
        for idx in raw_indices:
            if isinstance(idx, (list, tuple, np.ndarray)):
                indices.append(int(idx[0]))
            else:
                indices.append(int(idx))

        indices = sorted(set(indices), key=lambda i: scores[i], reverse=True)
        return [
            Detection(
                bbox=(int(boxes[i][0]), int(boxes[i][1]), int(boxes[i][2]), int(boxes[i][3])),
                confidence=float(scores[i]),
            )
            for i in indices
        ]


class ReIDEmbeddingExtractor:
    """Person ReID embedding extractor with ONNX support and histogram fallback."""

    def __init__(
        self,
        mode: str = "auto",
        model_path: str = "models/person_reid.onnx",
        input_width: int = 128,
        input_height: int = 256,
        output_dim: int = 512,
    ) -> None:
        self._logger = get_logger("detector.reid")
        self._mode = (mode or "auto").strip().lower()
        if self._mode not in {"auto", "onnx", "hist"}:
            self._logger.warning("unknown reid mode=%s, fallback to auto", mode)
            self._mode = "auto"

        self._input_width = max(16, int(input_width))
        self._input_height = max(16, int(input_height))
        self._output_dim = max(32, int(output_dim))
        self._net: Any | None = None
        self._ort_session: Any | None = None
        self._ort_input_name: str | None = None
        self._ort_input_shape: tuple[Any, ...] | None = None
        self._ov_compiled_model: Any | None = None
        self._ov_input: Any | None = None
        self._ov_output: Any | None = None
        self._onnx_ready = False
        self._backend_name = "hist"

        if self._mode in {"auto", "onnx"}:
            resolved = self._resolve_model_path(model_path)
            if resolved is None:
                self._logger.warning(
                    "ReID model file not found at %s; extractor will fallback to histogram embedding",
                    model_path,
                )
            else:
                try:
                    suffix = resolved.suffix.strip().lower()
                    if suffix == ".onnx":
                        self._net = None
                        try:
                            import onnxruntime as ort

                            self._ort_session = ort.InferenceSession(
                                str(resolved),
                                providers=["CPUExecutionProvider"],
                            )
                            ort_inputs = self._ort_session.get_inputs()
                            if not ort_inputs:
                                raise ValueError("onnxruntime session has no inputs")
                            self._ort_input_name = str(ort_inputs[0].name)
                            self._ort_input_shape = tuple(ort_inputs[0].shape)
                        except Exception as ort_exc:
                            self._logger.warning(
                                "failed to init onnxruntime for %s error=%s; fallback to OpenCV DNN",
                                resolved,
                                ort_exc,
                            )
                            self._ort_session = None
                            self._ort_input_name = None
                            self._ort_input_shape = None
                            self._net = cv2.dnn.readNetFromONNX(str(resolved))
                    elif suffix == ".xml":
                        self._net = None
                        try:
                            import openvino as ov

                            core = ov.Core()
                            model = core.read_model(model=str(resolved))
                            compiled = core.compile_model(model=model, device_name="CPU")
                            self._ov_compiled_model = compiled
                            self._ov_input = compiled.input(0)
                            self._ov_output = compiled.output(0)
                        except Exception as ov_exc:
                            self._logger.warning("failed to init OpenVINO runtime for %s error=%s", resolved, ov_exc)
                            self._net = cv2.dnn.readNet(str(resolved))
                    else:
                        self._logger.warning(
                            "unsupported ReID model suffix=%s path=%s; fallback to histogram embedding",
                            suffix or "<none>",
                            resolved,
                        )
                        self._net = None
                    if self._net is None and self._ov_compiled_model is None and self._ort_session is None:
                        raise ValueError("unable to create cv2.dnn network")
                    if self._net is not None and os.getenv("REID_ONNX_USE_CUDA", "").strip().lower() in {"1", "true", "yes"}:
                        try:
                            self._net.setPreferableBackend(cv2.dnn.DNN_BACKEND_CUDA)
                            self._net.setPreferableTarget(cv2.dnn.DNN_TARGET_CUDA)
                        except Exception:
                            # keep CPU backend when CUDA backend is unavailable
                            pass
                    self._onnx_ready = True
                    if self._ov_compiled_model is not None:
                        self._backend_name = "openvino"
                    elif self._ort_session is not None:
                        self._backend_name = "onnxruntime"
                    else:
                        self._backend_name = "dnn"
                    self._logger.info(
                        "ReID ONNX extractor enabled model=%s input=%dx%d out_dim=%d",
                        resolved,
                        self._input_width,
                        self._input_height,
                        self._output_dim,
                    )
                except Exception as exc:
                    self._logger.warning(
                        "failed to load ReID ONNX model=%s error=%s; extractor will fallback to histogram embedding",
                        resolved,
                        exc,
                    )

    @property
    def backend_name(self) -> str:
        return self._backend_name

    @property
    def is_onnx_ready(self) -> bool:
        return self._onnx_ready

    def _resolve_model_path(self, model_path: str) -> Path | None:
        path = Path(model_path)
        candidates = [path]
        if not path.is_absolute():
            backend_root = Path(__file__).resolve().parents[2]
            repo_root = backend_root.parent
            candidates = [
                Path.cwd() / path,
                backend_root / path,
                repo_root / path,
            ]
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return None

    def _fit_output_dim(self, vec: np.ndarray) -> np.ndarray:
        out = vec.astype(np.float32).reshape(-1)
        if out.size == 0:
            out = np.zeros((self._output_dim,), dtype=np.float32)
        if out.size >= self._output_dim:
            out = out[: self._output_dim]
        else:
            padded = np.zeros((self._output_dim,), dtype=np.float32)
            padded[: out.size] = out
            out = padded
        norm = float(np.linalg.norm(out))
        if norm > 1e-12:
            out = out / norm
        return out

    def _hist_embedding(self, image: np.ndarray) -> np.ndarray:
        if image is None or image.size == 0:
            return np.zeros((self._output_dim,), dtype=np.float32)
        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
        hist_h = cv2.calcHist([hsv], [0], None, [16], [0, 180]).flatten()
        hist_s = cv2.calcHist([hsv], [1], None, [16], [0, 256]).flatten()
        hist_v = cv2.calcHist([hsv], [2], None, [16], [0, 256]).flatten()
        raw = np.concatenate([hist_h, hist_s, hist_v]).astype(np.float32)
        total = float(raw.sum())
        if total > 0:
            raw /= total
        return self._fit_output_dim(raw)

    def _onnx_embedding(self, image: np.ndarray) -> np.ndarray | None:
        if not self._onnx_ready:
            return None
        if image is None or image.size == 0:
            return None
        try:
            resized = cv2.resize(
                image,
                (self._input_width, self._input_height),
                interpolation=cv2.INTER_LINEAR,
            )
            rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
            mean = np.asarray([0.485, 0.456, 0.406], dtype=np.float32).reshape(1, 1, 3)
            std = np.asarray([0.229, 0.224, 0.225], dtype=np.float32).reshape(1, 1, 3)
            normed = (rgb - mean) / std
            blob = np.transpose(normed, (2, 0, 1))[np.newaxis, :, :, :]
            if self._ov_compiled_model is not None and self._ov_input is not None and self._ov_output is not None:
                infer_result = self._ov_compiled_model({self._ov_input: blob})
                output = infer_result[self._ov_output]
            elif self._ort_session is not None and self._ort_input_name:
                ort_blob = self._prepare_ort_input(blob)
                output = self._ort_session.run(None, {self._ort_input_name: ort_blob})[0]
            elif self._net is not None:
                self._net.setInput(blob)
                output = self._net.forward()
            else:
                return None
            vec = np.asarray(output, dtype=np.float32).reshape(-1)
            raw_array = np.asarray(output, dtype=np.float32)
            if raw_array.ndim >= 2:
                vec = raw_array[0].reshape(-1)
            else:
                vec = raw_array.reshape(-1)
            if vec.size == 0:
                return None
            return self._fit_output_dim(vec)
        except Exception as exc:
            self._logger.warning("ReID ONNX forward failed (%s); fallback to histogram embedding", exc)
            self._onnx_ready = False
            self._backend_name = "hist"
            return None

    def _prepare_ort_input(self, blob: np.ndarray) -> np.ndarray:
        if self._ort_input_shape is None:
            return blob
        if len(self._ort_input_shape) != 4:
            return blob
        batch_dim = self._ort_input_shape[0]
        if not isinstance(batch_dim, int) or batch_dim <= 1:
            return blob
        if blob.shape[0] == batch_dim:
            return blob
        return np.repeat(blob, batch_dim, axis=0)

    def extract(self, image: np.ndarray) -> list[float]:
        if image is None or image.size == 0:
            return [0.0] * self._output_dim
        if self._mode == "hist":
            vec = self._hist_embedding(image)
        else:
            vec = self._onnx_embedding(image)
            if vec is None:
                vec = self._hist_embedding(image)
        return [round(float(v), 6) for v in vec.tolist()]


class PersonDetector:
    """Person detector with YOLO ONNX support and automatic HOG fallback.

    mode:
    - "auto": use YOLO if available, fallback to HOG when unavailable/error
    - "yolo": YOLO only (empty result when model unavailable)
    - "hog": HOG baseline only
    """

    def __init__(
        self,
        mode: str = "auto",
        hit_threshold: float = 0.0,
        yolo_model_path: str = "models/yolov8n.onnx",
        yolo_input_size: int = 640,
        confidence_threshold: float = 0.35,
        nms_threshold: float = 0.45,
        yolo_person_class_ids: Iterable[int] = (0,),
    ) -> None:
        self._logger = get_logger("detector.person")
        self._mode = mode.strip().lower() if mode else "auto"
        if self._mode not in {"auto", "yolo", "hog"}:
            self._logger.warning("unknown detector mode=%s, fallback to auto", mode)
            self._mode = "auto"

        self._hog = _HogPersonDetector(hit_threshold=hit_threshold)
        self._yolo: _YoloOnnxPersonDetector | None = None
        if self._mode in {"auto", "yolo"}:
            self._yolo = _YoloOnnxPersonDetector(
                model_path=yolo_model_path,
                input_size=yolo_input_size,
                confidence_threshold=confidence_threshold,
                nms_threshold=nms_threshold,
                person_class_ids=yolo_person_class_ids,
            )

    def detect(self, frame: np.ndarray) -> list[Detection]:
        if frame is None or frame.size == 0:
            return []

        if self._mode == "hog":
            return self._hog.detect(frame)

        if self._yolo is not None and self._yolo.is_ready:
            yolo_detections = self._yolo.detect(frame)
            if self._mode == "yolo":
                return yolo_detections
            return yolo_detections

        if self._mode == "yolo":
            return []

        return self._hog.detect(frame)
