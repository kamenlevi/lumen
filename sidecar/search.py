"""Text → embed → top-K cosine via sqlite-vec.

CLI:
    python -m sidecar.search "sunset over water"
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from dataclasses import dataclass
from typing import Any

from . import clip_model, db


@dataclass
class SearchResult:
    id: int
    score: float
    path: str
    thumb_path: str | None
    w: int | None
    h: int | None
    taken_at: str | None
    camera: str | None
    lat: float | None
    lon: float | None
    # Tier-2 quality (None until the quality pass has run on the image)
    sharpness: float | None = None
    is_blurry: int | None = None
    is_dark: int | None = None
    is_bright: int | None = None
    subject_out_of_focus: int | None = None
    dominant_hex: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


# Color name → the 30° hue bins (0=red..11=pink) that count toward it.
COLOR_BINS: dict[str, list[int]] = {
    "red": [11, 0],
    "orange": [0, 1],
    "golden": [0, 1],
    "yellow": [1, 2],
    "green": [2, 3, 4],
    "teal": [5, 6],
    "cyan": [5, 6],
    "blue": [6, 7, 8],
    "purple": [8, 9],
    "violet": [8, 9],
    "pink": [9, 10, 11],
    "magenta": [10, 11],
}


# Map a quality intent keyword to a SQL predicate over quality_metrics.
QUALITY_FILTERS: dict[str, str] = {
    "blurry": "q.is_blurry = 1",
    "sharp": "q.is_blurry = 0 AND q.sharpness IS NOT NULL",
    "dark": "q.is_dark = 1",
    "bright": "q.is_bright = 1",
    "out_of_focus": "q.subject_out_of_focus = 1",
}

# How to order each quality query so the most relevant land first.
QUALITY_ORDER: dict[str, str] = {
    "blurry": "q.sharpness ASC",
    "sharp": "q.sharpness DESC",
    "dark": "q.brightness ASC",
    "bright": "q.brightness DESC",
    "out_of_focus": "q.focus_ratio ASC",
}


def _model_settings(conn: sqlite3.Connection) -> tuple[str, str]:
    name = db.get_setting(conn, "model_name", clip_model.DEFAULT_MODEL)
    pretrained = db.get_setting(conn, "pretrained", clip_model.DEFAULT_PRETRAINED)
    return name or clip_model.DEFAULT_MODEL, pretrained or clip_model.DEFAULT_PRETRAINED


def _build_filter_sql(
    folder: str | None,
    camera: str | None,
    date_from: str | None,
    date_to: str | None,
    has_gps: bool | None,
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if folder:
        clauses.append("images.path LIKE ?")
        params.append(folder.rstrip("/") + "/%")
    if camera:
        clauses.append("images.camera = ?")
        params.append(camera)
    if date_from:
        clauses.append("images.taken_at >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("images.taken_at <= ?")
        params.append(date_to)
    if has_gps is True:
        clauses.append("images.lat IS NOT NULL AND images.lon IS NOT NULL")
    elif has_gps is False:
        clauses.append("(images.lat IS NULL OR images.lon IS NULL)")
    return (" AND ".join(clauses), params) if clauses else ("", params)


def search_by_vector(
    conn: sqlite3.Connection,
    query_vec: list[float],
    *,
    top_k: int = 50,
    offset: int = 0,
    folder: str | None = None,
    camera: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    has_gps: bool | None = None,
    exclude_id: int | None = None,
) -> list[SearchResult]:
    top_k = max(1, min(int(top_k), 500))
    where, params = _build_filter_sql(folder, camera, date_from, date_to, has_gps)
    extra = []
    if exclude_id is not None:
        extra.append("images.id != ?")
        params.append(exclude_id)
    if extra:
        where = " AND ".join([w for w in [where] + extra if w])

    # vec_search: order by distance ASC. Use match + k.
    sql = f"""
    SELECT images.id, images.path, images.thumb_path, images.w, images.h,
           images.taken_at, images.camera, images.lat, images.lon,
           q.sharpness, q.is_blurry, q.is_dark, q.is_bright, q.subject_out_of_focus,
           cm.dominant_hex,
           image_vecs.distance AS distance
      FROM image_vecs
      JOIN images ON images.id = image_vecs.id
 LEFT JOIN quality_metrics q ON q.image_id = images.id
 LEFT JOIN color_metrics cm ON cm.image_id = images.id
     WHERE image_vecs.embedding MATCH ?
       AND k = ?
       {('AND ' + where) if where else ''}
     ORDER BY image_vecs.distance ASC
     LIMIT ? OFFSET ?
    """
    bound = [db.pack_embedding(query_vec), top_k + offset, *params, top_k, offset]
    rows = conn.execute(sql, bound).fetchall()

    out: list[SearchResult] = []
    for r in rows:
        # sqlite-vec returns L2 distance on normalized vectors; convert to cosine sim.
        # ||a-b||^2 = 2 - 2*cos(a,b)  =>  cos = 1 - d^2 / 2
        d = float(r["distance"])
        score = max(0.0, 1.0 - (d * d) / 2.0)
        out.append(_row_to_result(r, score))
    return out


def _g(row: sqlite3.Row, key: str) -> Any:
    """Safely read an optional column that some queries don't select."""
    return row[key] if key in row.keys() else None


def _row_to_result(r, score: float) -> SearchResult:
    return SearchResult(
        id=r["id"], score=score, path=r["path"], thumb_path=r["thumb_path"],
        w=r["w"], h=r["h"], taken_at=r["taken_at"], camera=r["camera"],
        lat=r["lat"], lon=r["lon"],
        sharpness=_g(r, "sharpness"), is_blurry=_g(r, "is_blurry"),
        is_dark=_g(r, "is_dark"), is_bright=_g(r, "is_bright"),
        subject_out_of_focus=_g(r, "subject_out_of_focus"),
        dominant_hex=_g(r, "dominant_hex"),
    )


def search_by_color(
    color: str,
    *,
    top_k: int = 50,
    min_fraction: float = 0.06,
    candidate_ids: list[int] | None = None,
) -> list[SearchResult]:
    """Find photos containing a named color, scored by how much of that hue
    they contain. Special words: 'colorful'/'vibrant', 'monochrome'/'black and
    white'. Scoring runs in Python over the stored hue histograms."""
    import json as _json

    conn = db.connect()
    where = ""
    params: list[Any] = []
    if candidate_ids is not None:
        if not candidate_ids:
            return []
        where = f" AND images.id IN ({','.join('?' * len(candidate_ids))})"
        params.extend(candidate_ids)
    rows = conn.execute(f"""
        SELECT images.id, images.path, images.thumb_path, images.w, images.h,
               images.taken_at, images.camera, images.lat, images.lon,
               q.sharpness, q.is_blurry, q.is_dark, q.is_bright, q.subject_out_of_focus,
               cm.color_hist, cm.colorfulness, cm.dominant_hex
          FROM images
          JOIN color_metrics cm ON cm.image_id = images.id
     LEFT JOIN quality_metrics q ON q.image_id = images.id
         WHERE cm.color_hist IS NOT NULL{where}
    """, params).fetchall()

    word = color.lower()
    scored: list[tuple[float, Any]] = []
    for r in rows:
        cf = r["colorfulness"] or 0.0
        if word in ("colorful", "vibrant", "colourful"):
            score = cf
            ok = cf >= 0.45
        elif word in ("monochrome", "mono", "grayscale", "greyscale", "black and white", "b&w"):
            score = 1.0 - cf
            ok = cf <= 0.06
        else:
            bins = COLOR_BINS.get(word)
            if not bins:
                return []
            hist = _json.loads(r["color_hist"])
            score = sum(hist[b] for b in bins)
            ok = score >= min_fraction
        if ok:
            scored.append((score, r))

    scored.sort(key=lambda t: t[0], reverse=True)
    return [_row_to_result(r, round(s, 4)) for s, r in scored[:max(1, min(top_k, 500))]]


def search_descriptions(query: str, *, top_k: int = 50) -> list[SearchResult]:
    """Keyword search over the VLM-generated descriptions (vlm_cards). Lets the
    chat find photos by content the model saw — text on a sign, a specific
    object — that CLIP may miss. Only covers photos that have been described."""
    q = query.strip()
    if not q:
        return []
    conn = db.connect()
    rows = conn.execute(f"""
        SELECT images.id, images.path, images.thumb_path, images.w, images.h,
               images.taken_at, images.camera, images.lat, images.lon,
               qm.sharpness, qm.is_blurry, qm.is_dark, qm.is_bright,
               qm.subject_out_of_focus, cm.dominant_hex
          FROM vlm_cards v
          JOIN images ON images.id = v.image_id
     LEFT JOIN quality_metrics qm ON qm.image_id = images.id
     LEFT JOIN color_metrics cm ON cm.image_id = images.id
         WHERE v.description LIKE ? COLLATE NOCASE
         LIMIT ?
    """, (f"%{q}%", max(1, min(int(top_k), 500)))).fetchall()
    return [_row_to_result(r, 1.0) for r in rows]


def search_by_quality(
    flag: str,
    *,
    top_k: int = 50,
    candidate_ids: list[int] | None = None,
) -> list[SearchResult]:
    """Return photos matching a Tier-2 quality flag (blurry/sharp/dark/...),
    ordered by how strongly they match. If `candidate_ids` is given, restrict
    to those (used to combine a CLIP content search with a quality filter)."""
    if flag not in QUALITY_FILTERS:
        return []
    conn = db.connect()
    where = QUALITY_FILTERS[flag]
    order = QUALITY_ORDER.get(flag, "q.image_id")
    params: list[Any] = []
    extra = ""
    if candidate_ids is not None:
        if not candidate_ids:
            return []
        qs = ",".join("?" * len(candidate_ids))
        extra = f" AND images.id IN ({qs})"
        params.extend(candidate_ids)
    sql = f"""
    SELECT images.id, images.path, images.thumb_path, images.w, images.h,
           images.taken_at, images.camera, images.lat, images.lon,
           q.sharpness, q.is_blurry, q.is_dark, q.is_bright, q.subject_out_of_focus,
           cm.dominant_hex
      FROM images
      JOIN quality_metrics q ON q.image_id = images.id
 LEFT JOIN color_metrics cm ON cm.image_id = images.id
     WHERE {where}{extra}
     ORDER BY {order}
     LIMIT ?
    """
    params.append(max(1, min(int(top_k), 500)))
    rows = conn.execute(sql, params).fetchall()
    return [_row_to_result(r, 1.0) for r in rows]


def search_text(query: str, **kwargs) -> list[SearchResult]:
    conn = db.connect()
    name, pretrained = _model_settings(conn)
    bundle = clip_model.get_model(name, pretrained)
    db.init_db(conn, bundle.dim)
    vec = clip_model.encode_text(bundle, query).tolist()
    return search_by_vector(conn, vec, **kwargs)


def similar_to(image_id: int, **kwargs) -> list[SearchResult]:
    conn = db.connect()
    name, pretrained = _model_settings(conn)
    bundle = clip_model.get_model(name, pretrained)
    db.init_db(conn, bundle.dim)
    row = conn.execute(
        "SELECT embedding FROM image_vecs WHERE id = ?", (image_id,)
    ).fetchone()
    if not row:
        return []
    import struct
    blob = row["embedding"]
    floats = list(struct.unpack(f"{len(blob)//4}f", blob))
    kwargs.setdefault("exclude_id", image_id)
    return search_by_vector(conn, floats, **kwargs)


def _try_resident_server(query: str, **kwargs) -> list[SearchResult] | None:
    """If a sidecar server is running (announced via port file), use it.
    Avoids the 3-5s torch cold start on every CLI call. Returns None on
    any failure so the caller can fall back to in-process search."""
    from .paths import port_file
    pf = port_file()
    if not pf.exists():
        return None
    try:
        import json
        import urllib.error
        import urllib.request
        port = int(pf.read_text().strip())
        payload = {"query": query, **{k: v for k, v in kwargs.items() if v is not None}}
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/search",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode())
        return [SearchResult(**row) for row in data.get("results", [])]
    except (OSError, urllib.error.URLError, ValueError):
        return None


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Search indexed photos by text query.")
    p.add_argument("query", help="Natural language query.")
    p.add_argument("-k", "--top-k", type=int, default=20)
    p.add_argument("--folder", default=None)
    p.add_argument("--camera", default=None)
    p.add_argument("--from", dest="date_from", default=None)
    p.add_argument("--to", dest="date_to", default=None)
    p.add_argument("--has-gps", action="store_true", default=None)
    p.add_argument(
        "--no-server", action="store_true",
        help="Skip the resident server and load the model in-process.",
    )
    args = p.parse_args(argv)

    kwargs = dict(
        top_k=args.top_k, folder=args.folder, camera=args.camera,
        date_from=args.date_from, date_to=args.date_to, has_gps=args.has_gps,
    )
    results: list[SearchResult] | None = None
    if not args.no_server:
        results = _try_resident_server(args.query, **kwargs)
    if results is None:
        results = search_text(args.query, **kwargs)
    if not results:
        print("(no results)")
        return 0
    width = max(len(r.path) for r in results)
    for r in results:
        print(f"{r.score:.3f}  {r.path.ljust(width)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
