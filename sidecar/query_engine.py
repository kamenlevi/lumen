"""One query brain shared by every search surface.

Both the /search endpoint (compact bar, CLI) and the chat route queries
through here, so "blurry photos of the city" gives the SAME answer no matter
where you type it. Routing order (cheapest, most definitive signal first):

  1. quality intent  → filter the computed sharpness/exposure metrics
  2. color intent    → score the stored hue histograms
  3. everything else → object detector hits, then VLM descriptions, then CLIP

Intent detection is deliberately regex/keyword based — never an LLM — so
routing is instant, deterministic, and testable.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from .search import (
    COLOR_BINS,
    QUALITY_FILTERS,
    SearchResult,
    detect_object_label,
    search_by_color,
    search_by_object,
    search_by_quality,
    search_descriptions,
    search_text,
)

DEFAULT_RESULT_K = 24
# When combining content + quality/color, pull a wider CLIP net first, then filter.
CONTENT_CANDIDATE_K = 200

# Color words we can answer with the cheap hue histogram (no model).
_COLOR_SPECIAL = ["colorful", "colourful", "vibrant", "monochrome",
                  "black and white", "grayscale", "greyscale", "b&w"]
_COLOR_WORDS = list(COLOR_BINS.keys()) + _COLOR_SPECIAL

# Phrases that signal a quality intent → (flag, human label). Multi-word
# phrases are checked first so "out of focus" wins over "focus".
QUALITY_PHRASES: list[tuple[str, str, str]] = [
    (r"eyes (are |is )?(closed|shut)|closed eyes|shut eyes|blink(ed|ing)?|someone blinked|who blinked",
     "eyes_closed", "closed eyes"),
    (r"everyone.{0,15}eyes open|all eyes open|everyone.{0,15}looking|nobody blinked|eyes open",
     "eyes_open", "everyone's eyes open"),
    (r"soft eyes|eyes (are )?(not sharp|blurry|soft|out of focus)|blurry eyes|eyes not in focus",
     "soft_eyes", "soft / unsharp eyes"),
    (r"\b(faces|portraits?|people|persons?|headshots?)\b",
     "has_faces", "people"),
    (r"subject.{0,20}out of focus|out of focus.{0,20}subject|missed focus|"
     r"focus on the background|background (is )?in focus",
     "out_of_focus", "subject out of focus"),
    (r"out[- ]of[- ]focus|blurry|blurred|\bblur\b|not sharp|soft focus|motion blur",
     "blurry", "blurry"),
    (r"\bsharp(est)?\b|in focus|crisp|tack sharp", "sharp", "sharp"),
    (r"under[- ]?exposed|too dark|\bdark\b|dim\b|underexposed", "dark", "dark / underexposed"),
    (r"over[- ]?exposed|too bright|blown out|overexposed|\bbright\b", "bright", "bright / overexposed"),
]


def detect_quality(text: str) -> tuple[str, str, str] | None:
    """Return (flag, label, matched_phrase) if the text names a quality intent."""
    low = text.lower()
    for pattern, flag, label in QUALITY_PHRASES:
        m = re.search(pattern, low)
        if m:
            return flag, label, m.group(0)
    return None


def detect_color(text: str) -> str | None:
    low = text.lower()
    for word in sorted(_COLOR_WORDS, key=len, reverse=True):  # multiword first
        if re.search(rf"\b{re.escape(word)}\b", low):
            return word
    return None


def residual_content(text: str, extra_words: list[str] | None = None) -> str:
    """Strip quality/color words so the rest can be a CLIP content query
    ("blurry photos of the city" → "city")."""
    low = text
    for pattern, _flag, _label in QUALITY_PHRASES:
        low = re.sub(pattern, " ", low, flags=re.IGNORECASE)
    for word in extra_words or []:
        low = re.sub(rf"\b{re.escape(word)}\b", " ", low, flags=re.IGNORECASE)
    # drop filler so a bare "blurry photos" leaves nothing to CLIP-search
    low = re.sub(r"\b(photos?|pictures?|images?|shots?|subjects?|colou?red?|eyes?|open|closed|everyone|"
                 r"looking|faces?|people|persons?|portraits?|headshots?|soft|where|whose|who|"
                 r"of|my|the|all|show|me|find|with|that|are|is|which|ones?)\b",
                 " ", low, flags=re.IGNORECASE)
    return " ".join(low.split())


@dataclass
class Filters:
    """Metadata restrictions that any query kind can carry."""
    folder: str | None = None
    camera: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    has_gps: bool | None = None

    def any(self) -> bool:
        return any(v is not None for v in
                   (self.folder, self.camera, self.date_from, self.date_to, self.has_gps))

    def kwargs(self) -> dict[str, Any]:
        return {"folder": self.folder, "camera": self.camera,
                "date_from": self.date_from, "date_to": self.date_to,
                "has_gps": self.has_gps}


@dataclass
class QueryAnswer:
    """What the engine decided + found. `kind` tells a chat UI how to phrase
    the reply; a plain search surface can just render `results`."""
    kind: str                      # 'empty_library' | 'quality' | 'color' | 'search'
    results: list[SearchResult] = field(default_factory=list)
    # quality intent
    quality_flag: str | None = None
    quality_label: str | None = None
    total_matching: int | None = None   # library-wide count for a pure quality query
    # color intent
    color: str | None = None
    # residual CLIP content ("city" out of "blurry photos of the city")
    content: str | None = None
    # default search breakdown
    detected_object: str | None = None
    object_hits: int = 0
    description_hits: int = 0
    # set when the intent needs an analysis pass that hasn't run yet
    missing_data: str | None = None     # 'quality' | 'color'


def _filtered_ids(conn: sqlite3.Connection, filters: Filters) -> list[int] | None:
    """Image ids matching the metadata filters, or None when unrestricted."""
    if not filters.any():
        return None
    from .search import _build_filter_sql
    where, params = _build_filter_sql(**filters.kwargs())
    sql = f"SELECT id FROM images{' WHERE ' + where if where else ''}"
    return [r["id"] for r in conn.execute(sql, params)]


def _intersect(a: list[int] | None, b: list[int] | None) -> list[int] | None:
    if a is None:
        return b
    if b is None:
        return a
    bs = set(b)
    return [x for x in a if x in bs]


def run_query(
    conn: sqlite3.Connection,
    query: str,
    *,
    top_k: int = DEFAULT_RESULT_K,
    filters: Filters | None = None,
) -> QueryAnswer:
    filters = filters or Filters()
    query = query.strip()

    # A brand-new library can't answer anything — surfaces should guide the
    # user to add a folder instead of showing a misleading "no matches".
    if not conn.execute("SELECT 1 FROM images LIMIT 1").fetchone():
        return QueryAnswer(kind="empty_library")

    quality = detect_quality(query)
    if quality:
        return _run_quality(conn, query, quality, top_k, filters)

    color = detect_color(query)
    if color:
        return _run_color(conn, query, color, top_k, filters)

    return _run_search(conn, query, top_k, filters)


def _run_quality(conn, query, quality, top_k, filters: Filters) -> QueryAnswer:
    flag, label, _phrase = quality
    ans = QueryAnswer(kind="quality", quality_flag=flag, quality_label=label)

    if not conn.execute("SELECT 1 FROM quality_metrics LIMIT 1").fetchone():
        ans.missing_data = "quality"
        return ans

    ans.content = residual_content(query) or None
    cand: list[int] | None = None
    if ans.content:
        # Narrow by content with CLIP (already metadata-filtered), then keep
        # only those matching the quality flag.
        clip_hits = search_text(ans.content, top_k=CONTENT_CANDIDATE_K, **filters.kwargs())
        cand = [r.id for r in clip_hits]
    elif filters.any():
        cand = _filtered_ids(conn, filters)

    ans.results = search_by_quality(flag, top_k=top_k, candidate_ids=cand)
    if not ans.content and not filters.any():
        ans.total_matching = conn.execute(
            f"SELECT COUNT(*) AS n FROM quality_metrics q WHERE {QUALITY_FILTERS[flag]}"
        ).fetchone()["n"]
    return ans


def _run_color(conn, query, color, top_k, filters: Filters) -> QueryAnswer:
    ans = QueryAnswer(kind="color", color=color)

    if not conn.execute("SELECT 1 FROM color_metrics LIMIT 1").fetchone():
        ans.missing_data = "color"
        return ans

    ans.content = residual_content(query, extra_words=[color]) or None
    cand: list[int] | None = None
    if ans.content:
        clip_hits = search_text(ans.content, top_k=CONTENT_CANDIDATE_K, **filters.kwargs())
        cand = [r.id for r in clip_hits]
    elif filters.any():
        cand = _filtered_ids(conn, filters)

    ans.results = search_by_color(color, top_k=top_k, candidate_ids=cand)
    return ans


def _run_search(conn, query, top_k, filters: Filters) -> QueryAnswer:
    """Plain search, best signal first:
      1. object detector found it (definitive — "person", "car", "dog"…)
      2. an AI description mentions it
      3. CLIP semantic similarity (everything else)
    """
    ans = QueryAnswer(kind="search")
    fid = _filtered_ids(conn, filters)

    ans.detected_object = detect_object_label(query)
    objs = (search_by_object(ans.detected_object, top_k=top_k, candidate_ids=fid)
            if ans.detected_object else [])
    desc = search_descriptions(query, top_k=top_k)
    if fid is not None:
        allowed = set(fid)
        desc = [r for r in desc if r.id in allowed]
    clip = search_text(query, top_k=top_k, **filters.kwargs())

    ans.object_hits = len(objs)
    ans.description_hits = len(desc)
    seen: set[int] = set()
    merged: list[SearchResult] = []
    for r in objs + desc + clip:
        if r.id not in seen:
            seen.add(r.id)
            merged.append(r)
    ans.results = merged[:top_k]
    return ans
