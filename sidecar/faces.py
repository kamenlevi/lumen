"""Fast, trustworthy face/eye analysis via MediaPipe (CPU, ~50ms/image).

Gives the objective, photographer-grade checks that a global sharpness number
or a vague VLM cannot: how many faces, whose eyes are closed (blink blendshape),
and how sharp the eyes/face actually are (measured ON the face, not the frame).
"""

from __future__ import annotations

import threading
import urllib.request

import cv2
import numpy as np

from .paths import model_cache_dir

MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/face_landmarker/"
    "face_landmarker/float16/1/face_landmarker.task"
)
BLINK_CLOSED = 0.5  # eyeBlink blendshape above this ⇒ that eye is shut

# Landmark indices bounding both eyes (for an eye-region sharpness crop).
_EYE_IDX = [33, 133, 159, 145, 158, 153, 160, 144, 157, 154,
            362, 263, 386, 374, 385, 380, 387, 373, 388, 390]

_landmarker = None
_lock = threading.Lock()
_unavailable = False


def _model_path():
    p = model_cache_dir() / "face_landmarker.task"
    if not p.exists():
        urllib.request.urlretrieve(MODEL_URL, p)
    return p


def _get():
    """Lazily build a single shared FaceLandmarker (model load is one-time)."""
    global _landmarker, _unavailable
    if _unavailable:
        return None
    with _lock:
        if _landmarker is None:
            try:
                from mediapipe.tasks import python
                from mediapipe.tasks.python import vision
                opts = vision.FaceLandmarkerOptions(
                    base_options=python.BaseOptions(model_asset_path=str(_model_path())),
                    num_faces=12,
                    output_face_blendshapes=True,
                )
                _landmarker = vision.FaceLandmarker.create_from_options(opts)
            except Exception:
                _unavailable = True
                return None
    return _landmarker


def _lapvar(gray: np.ndarray, x0: int, y0: int, x1: int, y1: int) -> float:
    h, w = gray.shape
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(w, x1), min(h, y1)
    sub = gray[y0:y1, x0:x1]
    if sub.size < 25:
        return 0.0
    return float(cv2.Laplacian(sub, cv2.CV_64F).var())


def analyze_faces(rgb: np.ndarray, gray: np.ndarray) -> list[dict]:
    """Return one dict per face: blink score, eyes_closed, face/eye sharpness,
    and relative size. `gray` is used (via normalized landmarks) for sharpness."""
    lm = _get()
    if lm is None:
        return []
    try:
        import mediapipe as mp
        res = lm.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb)))
    except Exception:
        return []

    h, w = gray.shape
    out: list[dict] = []
    blends = res.face_blendshapes or []
    for i, landmarks in enumerate(res.face_landmarks):
        bs = {b.category_name: b.score for b in blends[i]} if i < len(blends) else {}
        blink = max(bs.get("eyeBlinkLeft", 0.0), bs.get("eyeBlinkRight", 0.0))
        xs = [p.x for p in landmarks]
        ys = [p.y for p in landmarks]
        fx0, fy0 = int(min(xs) * w), int(min(ys) * h)
        fx1, fy1 = int(max(xs) * w), int(max(ys) * h)
        ex = [landmarks[k].x for k in _EYE_IDX]
        ey = [landmarks[k].y for k in _EYE_IDX]
        ex0, ey0 = int(min(ex) * w), int(min(ey) * h)
        ex1, ey1 = int(max(ex) * w), int(max(ey) * h)
        out.append({
            "blink": round(blink, 3),
            "eyes_closed": blink > BLINK_CLOSED,
            "face_sharp": round(_lapvar(gray, fx0, fy0, fx1, fy1), 1),
            "eye_sharp": round(_lapvar(gray, ex0, ey0, ex1, ey1), 1),
            "area_frac": round(((fx1 - fx0) * (fy1 - fy0)) / float(w * h), 4),
        })
    return out
