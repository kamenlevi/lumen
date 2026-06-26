"""Tier-2 classical-CV quality metrics.

No ML model — pure OpenCV/NumPy, fast (~10-30ms/image), cached once per image
in the `quality_metrics` table. This is what makes questions like
"are all my photos in focus?" answerable without running a heavy vision model
on every shot: cheap metrics narrow the field first.

Metrics per image:
  - sharpness            global variance-of-Laplacian (on a 1024px-normalized
                         grayscale, so the number is comparable across photos)
  - brightness           mean luma 0-255, plus shadow/highlight clipping
  - subject vs background the clever bit: detect the subject (face, else the
                         central region) and compare its sharpness to the
                         background. If the background is sharp but the subject
                         is soft, focus landed in the wrong place.
  - fnumber              EXIF aperture, used to avoid flagging intentional
                         shallow depth-of-field (bokeh) as a mistake.

CLI:
    python -m sidecar.quality /path/to/folder            # analyze + report
    python -m sidecar.quality /path/to/folder --store    # also write to DB
    python -m sidecar.quality /path/to/folder --limit 20 # first N images
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ExifTags

from . import faces as face_mod
from . import thumb

# ── Tuning thresholds ─────────────────────────────────────────────────────────
# All sharpness numbers are variance-of-Laplacian on a grayscale image whose
# longest side has been normalized to NORM_SIDE, so they're comparable.
NORM_SIDE = 1024

BLUR_SHARPNESS = 120.0      # global sharpness below this → blurry
DARK_BRIGHTNESS = 45.0      # mean luma below this → underexposed
BRIGHT_BRIGHTNESS = 215.0   # mean luma above this → overexposed
NEAR_BLACK = 16             # pixel <= this counts as crushed shadow
NEAR_WHITE = 240            # pixel >= this counts as blown highlight

# Subject-out-of-focus: subject sharpness as a fraction of background sharpness.
FOCUS_RATIO_FACE = 0.85     # a detected face softer than the background by >15%
FOCUS_RATIO_CENTER = 0.50   # weaker signal (no face) → demand stronger evidence
MIN_BG_SHARPNESS = 60.0     # background must actually be sharp, else it's just
                            # a soft photo (blurry), not a focus *miss*
WIDE_APERTURE = 2.8         # f-number below this → shallow DoF likely intended,
                            # so don't flag a soft center as a mistake

_FACE_CASCADE: "cv2.CascadeClassifier | None" = None
_EYE_CASCADE: "cv2.CascadeClassifier | None" = None


def _face_cascade() -> "cv2.CascadeClassifier":
    global _FACE_CASCADE
    if _FACE_CASCADE is None:
        path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        _FACE_CASCADE = cv2.CascadeClassifier(path)
    return _FACE_CASCADE


def _eye_cascade() -> "cv2.CascadeClassifier":
    global _EYE_CASCADE
    if _EYE_CASCADE is None:
        path = cv2.data.haarcascades + "haarcascade_eye.xml"
        _EYE_CASCADE = cv2.CascadeClassifier(path)
    return _EYE_CASCADE


def _has_eyes(gray: np.ndarray, face: tuple[int, int, int, int]) -> bool:
    """Haar frontal-face fires on textured patterns (windows, foliage, city
    grids). Real faces have detectable eyes; those false positives don't — so
    we only trust a face candidate if an eye is found inside it. Eyes sit in
    the upper ~60% of the face box, so we only search there."""
    x, y, w, h = face
    roi = gray[y:y + int(h * 0.6), x:x + w]
    if roi.size == 0:
        return False
    eyes = _eye_cascade().detectMultiScale(roi, scaleFactor=1.1, minNeighbors=4,
                                           minSize=(max(8, w // 12), max(8, h // 12)))
    return len(eyes) >= 1


def _fnumber(path: Path) -> float | None:
    """Read EXIF aperture (f-number) via Pillow — no exifread dependency."""
    try:
        exif = Image.open(path).getexif()
        if not exif:
            return None
        # FNumber (37386? no) — tag id 33437. Fall back to ApertureValue (37378).
        ifd = exif.get_ifd(ExifTags.IFD.Exif) if hasattr(ExifTags, "IFD") else {}
        fn = (ifd or {}).get(33437) or exif.get(33437)
        if fn is not None:
            val = float(fn)
            return val if val > 0 else None
        av = (ifd or {}).get(37378)
        if av is not None:
            # ApertureValue is APEX: f = sqrt(2) ** APEX
            return round(float(2.0 ** (float(av) / 2.0)), 1)
    except Exception:
        return None
    return None


def _normalize(img: Image.Image) -> tuple[np.ndarray, np.ndarray]:
    """RGB PIL → (rgb, gray) numpy arrays, longest side scaled to NORM_SIDE."""
    arr = np.asarray(img.convert("RGB"), dtype=np.uint8)
    h, w = arr.shape[:2]
    scale = NORM_SIDE / max(h, w)
    if scale < 1.0:
        arr = cv2.resize(arr, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    return arr, gray


def _detect_subject(gray: np.ndarray) -> tuple[tuple[int, int, int, int], str]:
    """Return (x, y, w, h) box of the subject and its source.
    Prefers the largest detected face; falls back to the central region."""
    h, w = gray.shape
    try:
        faces = _face_cascade().detectMultiScale(
            gray, scaleFactor=1.1, minNeighbors=5,
            minSize=(max(24, w // 20), max(24, h // 20)),
        )
    except Exception:
        faces = []
    # Largest first; accept the first candidate that actually has eyes inside
    # it (rejects Haar's texture false positives on cityscapes/foliage).
    for x, y, fw, fh in sorted(faces, key=lambda f: f[2] * f[3], reverse=True):
        if not _has_eyes(gray, (x, y, fw, fh)):
            continue
        # pad 20% so we capture the whole head, not just the eyes
        px, py = int(fw * 0.2), int(fh * 0.2)
        x, y = max(0, x - px), max(0, y - py)
        fw, fh = min(w - x, fw + 2 * px), min(h - y, fh + 2 * py)
        return (x, y, fw, fh), "face"
    # No face → assume the subject sits in the central region.
    cx0, cy0 = int(w * 0.25), int(h * 0.20)
    return (cx0, cy0, int(w * 0.50), int(h * 0.60)), "center"


def _region_var(lap: np.ndarray, box: tuple[int, int, int, int]) -> tuple[float, float]:
    """Variance of the Laplacian inside the box vs outside it."""
    x, y, w, h = box
    mask = np.zeros(lap.shape, dtype=bool)
    mask[y:y + h, x:x + w] = True
    inside = lap[mask]
    outside = lap[~mask]
    in_var = float(inside.var()) if inside.size else 0.0
    out_var = float(outside.var()) if outside.size else 0.0
    return in_var, out_var


def analyze(img: Image.Image, fnumber: float | None = None) -> dict:
    """Compute all Tier-2 metrics for an already-loaded RGB image.
    Returns a dict matching the quality_metrics columns."""
    rgb, gray = _normalize(img)
    lap = cv2.Laplacian(gray, cv2.CV_64F)

    sharpness = float(lap.var())
    brightness = float(gray.mean())
    clip_low = float((gray <= NEAR_BLACK).mean())
    clip_high = float((gray >= NEAR_WHITE).mean())

    box, source = _detect_subject(gray)
    subj_sharp, bg_sharp = _region_var(lap, box)
    focus_ratio = subj_sharp / bg_sharp if bg_sharp > 1e-6 else None

    is_blurry = sharpness < BLUR_SHARPNESS
    is_dark = brightness < DARK_BRIGHTNESS
    is_bright = brightness > BRIGHT_BRIGHTNESS

    # Subject-out-of-focus: only meaningful when the background is genuinely
    # sharp (otherwise the whole frame is soft → that's "blurry", not a miss).
    #
    # We only HARD-FLAG when there's an actual detected subject (a face): a soft
    # face over a sharp background is unambiguously a focus miss. The "center"
    # fallback is too noisy to flag — on subjectless landscapes/aerials it
    # false-positives — so we compute its ratio for later use (a real object
    # detector in a future step can act on it) but never raise the flag on it.
    subject_oof = False
    if (
        source == "face"
        and focus_ratio is not None
        and bg_sharp >= MIN_BG_SHARPNESS
        and not is_blurry
        and focus_ratio < FOCUS_RATIO_FACE
    ):
        subject_oof = True

    # Reliable face/eye checks via MediaPipe (blink, eyes-closed, eye sharpness).
    flist = face_mod.analyze_faces(rgb, gray)
    num_faces = len(flist)
    any_closed = any(f["eyes_closed"] for f in flist)
    main = max(flist, key=lambda f: f["area_frac"], default=None)

    return {
        "sharpness": round(sharpness, 2),
        "brightness": round(brightness, 2),
        "clip_low": round(clip_low, 4),
        "clip_high": round(clip_high, 4),
        "subject_source": source,
        "subject_sharpness": round(subj_sharp, 2),
        "background_sharpness": round(bg_sharp, 2),
        "focus_ratio": round(focus_ratio, 3) if focus_ratio is not None else None,
        "fnumber": fnumber,
        "is_blurry": int(is_blurry),
        "is_dark": int(is_dark),
        "is_bright": int(is_bright),
        "subject_out_of_focus": int(subject_oof),
        "num_faces": num_faces,
        "eyes_closed": int(any_closed),
        "eyes_open_all": int(num_faces > 0 and not any_closed),
        "eye_sharp": main["eye_sharp"] if main else None,
        "face_sharp": main["face_sharp"] if main else None,
        "analyzed_at": time.time(),
    }


def analyze_path(path: Path) -> dict:
    """Load an image from disk and analyze it."""
    img = thumb.load_image(path)
    return analyze(img, fnumber=_fnumber(path))


# ── CLI ───────────────────────────────────────────────────────────────────────

def _flags_str(m: dict) -> str:
    flags = []
    if m["is_blurry"]:
        flags.append("BLURRY")
    if m["is_dark"]:
        flags.append("DARK")
    if m["is_bright"]:
        flags.append("BRIGHT")
    if m["subject_out_of_focus"]:
        flags.append("SUBJECT-OOF")
    return ",".join(flags) if flags else "ok"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Tier-2 CV quality analysis of a folder.")
    p.add_argument("folder", type=Path, help="Folder of images (recursive).")
    p.add_argument("--store", action="store_true",
                   help="Write metrics to the DB for images already indexed.")
    p.add_argument("--limit", type=int, default=None, help="Only first N images.")
    args = p.parse_args(argv)

    root = args.folder.expanduser().resolve()
    if not root.is_dir():
        print(f"Not a directory: {root}", file=sys.stderr)
        return 2

    files = [p for p in sorted(root.rglob("*"))
             if p.is_file() and thumb.is_supported(p)
             and not any(part.startswith(".") for part in p.parts)]
    if args.limit:
        files = files[:args.limit]
    if not files:
        print("No supported images found.", file=sys.stderr)
        return 1

    conn = None
    if args.store:
        from . import db
        conn = db.connect()

    started = time.time()
    counts = {"BLURRY": 0, "DARK": 0, "BRIGHT": 0, "SUBJECT-OOF": 0, "ok": 0}
    flagged: list[tuple[Path, dict]] = []

    for i, path in enumerate(files, 1):
        try:
            m = analyze_path(path)
        except Exception as e:
            print(f"  ! failed {path.name}: {e}", file=sys.stderr)
            continue
        fl = _flags_str(m)
        for f in fl.split(","):
            counts[f] = counts.get(f, 0) + 1
        if fl != "ok":
            flagged.append((path, m))
        print(f"[{i}/{len(files)}] {path.name:45.45}  "
              f"sharp={m['sharpness']:8.1f}  bright={m['brightness']:5.1f}  "
              f"focus={m['focus_ratio']}  {m['subject_source']:6}  -> {fl}")

        if conn is not None:
            row = conn.execute("SELECT id FROM images WHERE path = ?",
                               (str(path),)).fetchone()
            if row:
                db.upsert_quality(conn, row["id"], m)
    if conn is not None:
        conn.commit()

    elapsed = time.time() - started
    print(f"\nAnalyzed {len(files)} images in {elapsed:.1f}s "
          f"({len(files)/max(elapsed,1e-3):.1f} img/s)")
    print(f"Flagged: blurry={counts.get('BLURRY',0)} dark={counts.get('DARK',0)} "
          f"bright={counts.get('BRIGHT',0)} subject-out-of-focus={counts.get('SUBJECT-OOF',0)}")
    if flagged:
        print("\nFlagged photos:")
        for path, m in flagged:
            print(f"  {path.name:45.45}  {_flags_str(m)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
