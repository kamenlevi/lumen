"""Subject-aware analysis (MediaPipe, CPU, fast): find the subject first, then
analyze it. An object detector locates people/objects; faces are then found by
cropping to each person (so small/distant faces in full-body shots are caught,
which a whole-image face detector misses); subject sharpness is measured on the
detected subject, not the whole frame (fixes "sharp 4000" on a blurred subject).
"""

from __future__ import annotations

import threading
import urllib.request

import cv2
import numpy as np

from .paths import model_cache_dir

FACE_URL = ("https://storage.googleapis.com/mediapipe-models/face_landmarker/"
            "face_landmarker/float16/1/face_landmarker.task")
OBJ_URL = ("https://storage.googleapis.com/mediapipe-models/object_detector/"
           "efficientdet_lite0/float32/1/efficientdet_lite0.tflite")
BLINK_CLOSED = 0.5

_EYE_IDX = [33, 133, 159, 145, 158, 153, 160, 144, 157, 154,
            362, 263, 386, 374, 385, 380, 387, 373, 388, 390]

_face = None
_obj = None
_lock = threading.Lock()
_unavailable = False


def _model(name: str, url: str) -> str:
    p = model_cache_dir() / name
    if not p.exists():
        urllib.request.urlretrieve(url, p)
    return str(p)


def _ensure() -> bool:
    global _face, _obj, _unavailable
    if _unavailable:
        return False
    with _lock:
        if _face is None:
            try:
                from mediapipe.tasks import python
                from mediapipe.tasks.python import vision
                _face = vision.FaceLandmarker.create_from_options(
                    vision.FaceLandmarkerOptions(
                        base_options=python.BaseOptions(
                            model_asset_path=_model("face_landmarker.task", FACE_URL)),
                        num_faces=5, output_face_blendshapes=True,
                        min_face_detection_confidence=0.3))
                _obj = vision.ObjectDetector.create_from_options(
                    vision.ObjectDetectorOptions(
                        base_options=python.BaseOptions(
                            model_asset_path=_model("efficientdet_lite0.tflite", OBJ_URL)),
                        score_threshold=0.3, max_results=15))
            except Exception:
                _unavailable = True
                return False
    return True


def _mpimg(arr: np.ndarray):
    import mediapipe as mp
    return mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(arr))


def _lapvar(gray: np.ndarray, x0: int, y0: int, x1: int, y1: int) -> float:
    h, w = gray.shape
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(w, x1), min(h, y1)
    sub = gray[y0:y1, x0:x1]
    return float(cv2.Laplacian(sub, cv2.CV_64F).var()) if sub.size >= 25 else 0.0


def _face_dict(landmarks, blends, ox: int, oy: int, sw: int, sh: int, gray: np.ndarray) -> dict:
    d = {b.category_name: b.score for b in blends} if blends else {}
    blink = max(d.get("eyeBlinkLeft", 0.0), d.get("eyeBlinkRight", 0.0))
    xs = [p.x for p in landmarks]
    ys = [p.y for p in landmarks]
    fx0, fy0 = ox + int(min(xs) * sw), oy + int(min(ys) * sh)
    fx1, fy1 = ox + int(max(xs) * sw), oy + int(max(ys) * sh)
    ex = [landmarks[k].x for k in _EYE_IDX]
    ey = [landmarks[k].y for k in _EYE_IDX]
    ex0, ey0 = ox + int(min(ex) * sw), oy + int(min(ey) * sh)
    ex1, ey1 = ox + int(max(ex) * sw), oy + int(max(ey) * sh)
    h, w = gray.shape
    return {
        "blink": round(blink, 3),
        "eyes_closed": blink > BLINK_CLOSED,
        "face_sharp": round(_lapvar(gray, fx0, fy0, fx1, fy1), 1),
        "eye_sharp": round(_lapvar(gray, ex0, ey0, ex1, ey1), 1),
        "area_frac": round(((fx1 - fx0) * (fy1 - fy0)) / float(w * h), 4),
    }


def analyze(rgb: np.ndarray, gray: np.ndarray) -> dict:
    """Return {faces, objects, subject}. faces = eye/blink/sharpness per face;
    subject = the main detected object with its sharpness."""
    empty = {"faces": [], "objects": [], "subject": None}
    if not _ensure():
        return empty
    h, w = gray.shape
    try:
        det = _obj.detect(_mpimg(rgb))
    except Exception:
        return empty

    objects = []
    for d in det.detections:
        bb = d.bounding_box
        objects.append({
            "label": d.categories[0].category_name,
            "score": round(d.categories[0].score, 2),
            "box": (bb.origin_x, bb.origin_y, bb.width, bb.height),
        })
    persons = [o for o in objects if o["label"] == "person"]

    # Faces: crop to each person (catches small/distant faces). If no people
    # detected, fall back to the whole image (close-up portraits).
    faces: list[dict] = []
    targets = [p["box"] for p in persons] if persons else [(0, 0, w, h)]
    for (x, y, bw, bh) in targets:
        head_h = int(bh * 0.6) if persons else bh   # face is in the upper body
        cx0, cy0 = max(0, x), max(0, y)
        cx1, cy1 = min(w, x + bw), min(h, y + head_h)
        crop = rgb[cy0:cy1, cx0:cx1]
        if crop.size == 0:
            continue
        try:
            r = _face.detect(_mpimg(crop))
        except Exception:
            continue
        sw, sh = cx1 - cx0, cy1 - cy0
        blends = r.face_blendshapes or []
        for i, lm in enumerate(r.face_landmarks):
            faces.append(_face_dict(lm, blends[i] if i < len(blends) else None,
                                    cx0, cy0, sw, sh, gray))

    subject = None
    if objects:
        m = max(objects, key=lambda o: o["box"][2] * o["box"][3])
        x, y, bw, bh = m["box"]
        subject = {
            "label": m["label"],
            "sharp": round(_lapvar(gray, x, y, x + bw, y + bh), 1),
            "area_frac": round((bw * bh) / float(w * h), 3),
        }
    return {"faces": faces, "objects": objects, "subject": subject}


def analyze_faces(rgb: np.ndarray, gray: np.ndarray) -> list[dict]:
    return analyze(rgb, gray)["faces"]
