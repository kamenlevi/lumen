"""Tier-2 color analysis: a dominant-hue histogram per image.

Cheap classical CV (no model). Computed from the cached 256px thumbnail — so
a 20MP RAW costs the same as a phone snap — and stored once. This is what makes
"find pink / blue / golden photos" instant and accurate; CLIP is notoriously
bad at color, this isn't.

CLI:
    python -m sidecar.color            # analyze all indexed images lacking color
    python -m sidecar.color --all      # recompute for everything
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from . import db, thumb

# 12 hue bins of 30°, starting at red. cv2 H is 0-179, so we double it to 0-358.
N_BINS = 12
# A pixel only "counts" as colorful if it's saturated enough and not too dark,
# so a grey sky or black shadow doesn't get assigned a hue.
MIN_SAT = 0.22
MIN_VAL = 0.18


def analyze_color(img: Image.Image) -> dict:
    arr = np.asarray(img.convert("RGB"), dtype=np.uint8)
    small = cv2.resize(arr, (64, 64), interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(small, cv2.COLOR_RGB2HSV)
    h = hsv[:, :, 0].astype(np.float32) * 2.0           # 0-358°
    s = hsv[:, :, 1].astype(np.float32) / 255.0
    v = hsv[:, :, 2].astype(np.float32) / 255.0
    total = h.size

    colorful = (s >= MIN_SAT) & (v >= MIN_VAL)
    colorfulness = float(colorful.mean())

    bins = (h[colorful] // 30).astype(int) % N_BINS
    counts = np.bincount(bins, minlength=N_BINS).astype(np.float32)
    hist = (counts / total).tolist()  # fraction of ALL pixels, per hue bin

    # Representative colour for a UI swatch: mean RGB of the dominant hue bin,
    # or the overall mean if the image is essentially greyscale.
    if colorful.any() and counts.max() > 0:
        dom = int(counts.argmax())
        mask = colorful & (((h // 30).astype(int) % N_BINS) == dom)
        rgb = small[mask].mean(axis=0)
    else:
        rgb = small.reshape(-1, 3).mean(axis=0)
    dominant_hex = "#{:02x}{:02x}{:02x}".format(*(int(c) for c in rgb))

    return {
        "color_hist": json.dumps([round(x, 4) for x in hist]),
        "colorfulness": round(colorfulness, 4),
        "dominant_hex": dominant_hex,
    }


def _load_for_color(thumb_path: str | None, image_path: str) -> Image.Image:
    """Prefer the 256px thumbnail (fast); fall back to the original."""
    if thumb_path and Path(thumb_path).exists():
        return Image.open(thumb_path)
    return thumb.load_image(Path(image_path))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Tier-2 color analysis of indexed photos.")
    p.add_argument("--all", action="store_true", help="Recompute for every image.")
    args = p.parse_args(argv)

    conn = db.connect()
    if args.all:
        rows = conn.execute("SELECT id, path, thumb_path FROM images").fetchall()
    else:
        rows = conn.execute(
            """SELECT i.id, i.path, i.thumb_path FROM images i
               LEFT JOIN color_metrics c ON c.image_id = i.id
               WHERE c.image_id IS NULL"""
        ).fetchall()
    if not rows:
        print("Nothing to analyze (all images already have color data).")
        return 0

    started = time.time()
    done = 0
    for r in rows:
        try:
            img = _load_for_color(r["thumb_path"], r["path"])
            db.upsert_color(conn, r["id"], analyze_color(img))
            done += 1
        except Exception as e:
            print(f"  ! {Path(r['path']).name}: {e}", file=sys.stderr)
        if done % 50 == 0:
            conn.commit()
    conn.commit()
    elapsed = time.time() - started
    print(f"Color-analyzed {done}/{len(rows)} images in {elapsed:.1f}s "
          f"({done / max(elapsed, 1e-3):.0f} img/s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
