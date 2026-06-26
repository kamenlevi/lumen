"""Chat brain: turn a user message into an assistant reply plus photo results.

This is a deliberately thin, swappable seam. It is NOT yet a full LLM agent
(that's a later step), but it already does more than keyword search: it
recognises Tier-2 *quality* intents ("blurry", "sharp", "dark", "out of
focus") and answers them by filtering the computed quality metrics — combined
with CLIP when the query also names content ("blurry photos of the city").
"""

from __future__ import annotations

import re
import sqlite3

from . import bursts as bursts_mod
from . import db
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

# Color words we can answer with the cheap hue histogram (no model).
_COLOR_SPECIAL = ["colorful", "colourful", "vibrant", "monochrome",
                  "black and white", "grayscale", "greyscale", "b&w"]
_COLOR_WORDS = list(COLOR_BINS.keys()) + _COLOR_SPECIAL

DEFAULT_RESULT_K = 24
# When combining content + quality, pull a wider CLIP net first, then filter.
CONTENT_CANDIDATE_K = 200

# Phrases that signal a quality intent → (flag, human label). Multi-word
# phrases are checked first so "out of focus" wins over "focus".
_QUALITY_PHRASES: list[tuple[str, str, str]] = [
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


def _title_from(text: str) -> str:
    t = " ".join(text.strip().split())
    return (t[:40] + "…") if len(t) > 40 else t or "New chat"


def _detect_quality(text: str) -> tuple[str, str, str] | None:
    """Return (flag, label, matched_phrase) if the text names a quality intent."""
    low = text.lower()
    for pattern, flag, label in _QUALITY_PHRASES:
        m = re.search(pattern, low)
        if m:
            return flag, label, m.group(0)
    return None


def _detect_color(text: str) -> str | None:
    low = text.lower()
    for word in sorted(_COLOR_WORDS, key=len, reverse=True):  # multiword first
        if re.search(rf"\b{re.escape(word)}\b", low):
            return word
    return None


def _residual_content(text: str, extra_words: list[str] | None = None) -> str:
    """Strip quality/color words so the rest can be a CLIP content query
    ("blurry photos of the city" → "city")."""
    low = text
    for pattern, _flag, _label in _QUALITY_PHRASES:
        low = re.sub(pattern, " ", low, flags=re.IGNORECASE)
    for word in extra_words or []:
        low = re.sub(rf"\b{re.escape(word)}\b", " ", low, flags=re.IGNORECASE)
    # drop filler so a bare "blurry photos" leaves nothing to CLIP-search
    low = re.sub(r"\b(photos?|pictures?|images?|shots?|subjects?|colou?red?|eyes?|open|closed|everyone|"
                 r"looking|faces?|people|persons?|portraits?|headshots?|soft|where|whose|who|"
                 r"of|my|the|all|show|me|find|with|that|are|is|which|ones?)\b",
                 " ", low, flags=re.IGNORECASE)
    return " ".join(low.split())


def _refs(results: list[SearchResult]) -> list[dict]:
    return [{"id": r.id, "score": round(r.score, 4)} for r in results]


def _have_quality_data(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT COUNT(*) AS n FROM quality_metrics").fetchone()
    return bool(row and row["n"])


def respond(
    conn: sqlite3.Connection,
    history: list[sqlite3.Row],
    user_text: str,
) -> tuple[str, list[dict]]:
    query = user_text.strip()
    if not query:
        return ("What would you like to find or know about your photos?", [])

    if re.search(r"\bburst|keeper|best (shot|photo|one|of)|near[- ]identical|duplicate|"
                 r"pick the best|which (one )?is best|similar shots|cull\b", query.lower()):
        return _answer_bursts(conn)

    quality = _detect_quality(query)
    if quality:
        return _answer_quality(conn, query, quality)

    color = _detect_color(query)
    if color:
        return _answer_color(conn, query, color)

    # Plain search, best signal first:
    #  1. object detector found it (definitive — "person", "car", "dog"…)
    #  2. an AI description mentions it
    #  3. CLIP semantic similarity (everything else)
    obj_label = detect_object_label(query)
    objs = search_by_object(obj_label, top_k=DEFAULT_RESULT_K) if obj_label else []
    desc = search_descriptions(query, top_k=DEFAULT_RESULT_K)
    clip = search_text(query, top_k=DEFAULT_RESULT_K)
    seen: set[int] = set()
    merged: list[SearchResult] = []
    for r in objs + desc + clip:
        if r.id not in seen:
            seen.add(r.id)
            merged.append(r)
    merged = merged[:DEFAULT_RESULT_K]
    if not merged:
        return (
            f"I couldn’t find anything matching “{query}”. Make sure the folder "
            "is indexed in the Library tab, or try describing it differently.",
            [],
        )
    bits = []
    if objs:
        bits.append(f"{len(objs)} where I detected a {obj_label}")
    if desc:
        bits.append(f"{len(desc)} from AI descriptions")
    note = (" — " + ", ".join(bits)) if bits else ""
    return (f"Here are {len(merged)} photos matching “{query}”{note}.", _refs(merged))


def _answer_bursts(conn: sqlite3.Connection) -> tuple[str, list[dict]]:
    bursts = bursts_mod.find_bursts(conn, limit_bursts=25)
    if not bursts:
        return (
            "I didn’t find any bursts — groups of near-identical shots taken a few "
            "seconds apart. (They need capture timestamps + visual similarity.)",
            [],
        )
    refs: list[dict] = []
    for burst in bursts:
        for p in burst:
            refs.append({"id": p["id"], "score": p["score"], "keeper": p["is_keeper"]})
    total_shots = sum(len(b) for b in bursts)
    return (
        f"Found {len(bursts)} burst{'s' if len(bursts) != 1 else ''} "
        f"({total_shots} shots). The recommended keeper in each is starred ★ — "
        f"the rest are near-duplicates you can cull.",
        refs,
    )


def _answer_color(
    conn: sqlite3.Connection,
    query: str,
    color: str,
) -> tuple[str, list[dict]]:
    if not conn.execute("SELECT COUNT(*) AS n FROM color_metrics").fetchone()["n"]:
        return (
            "I haven’t analysed your library’s colors yet. Once that quick pass "
            "has run, “find pink photos” becomes an instant filter.",
            [],
        )

    content = _residual_content(query, extra_words=[color])
    if content:
        clip_hits = search_text(content, top_k=CONTENT_CANDIDATE_K)
        cand_ids = [r.id for r in clip_hits]
        results = search_by_color(color, top_k=DEFAULT_RESULT_K, candidate_ids=cand_ids)
        if not results:
            return (f"None of your “{content}” photos are noticeably {color}.", [])
        return (
            f"Your {color} photos matching “{content}” — {len(results)}, "
            f"most {color} first.",
            _refs(results),
        )

    results = search_by_color(color, top_k=DEFAULT_RESULT_K)
    if not results:
        return (f"I didn’t find noticeably {color} photos in your library.", [])
    return (
        f"Here are your most {color} photos ({len(results)} shown, "
        f"strongest first). This uses each photo’s color makeup, not keywords.",
        _refs(results),
    )


def _answer_quality(
    conn: sqlite3.Connection,
    query: str,
    quality: tuple[str, str, str],
) -> tuple[str, list[dict]]:
    flag, label, _phrase = quality

    if not _have_quality_data(conn):
        return (
            "I haven’t analysed your library for image quality yet (sharpness, "
            "exposure, focus). Once that pass has run I can answer this — for now "
            "I can only do content search.",
            [],
        )

    content = _residual_content(query)
    if content:
        # Narrow by content with CLIP, then keep only those matching the quality flag.
        clip_hits = search_text(content, top_k=CONTENT_CANDIDATE_K)
        cand_ids = [r.id for r in clip_hits]
        results = search_by_quality(flag, top_k=DEFAULT_RESULT_K, candidate_ids=cand_ids)
        if not results:
            return (
                f"None of the photos matching “{content}” have {label}. "
                f"(Checked {len(cand_ids)} content matches.)",
                [],
            )
        return (
            f"Of your photos matching “{content}” — {len(results)} with {label}.",
            _refs(results),
        )

    # Pure quality query over the whole library.
    results = search_by_quality(flag, top_k=DEFAULT_RESULT_K)
    total = conn.execute(
        f"SELECT COUNT(*) AS n FROM quality_metrics q WHERE {QUALITY_FILTERS[flag]}"
    ).fetchone()["n"]
    if not results:
        return (f"No photos found with {label}.", [])
    shown = len(results)
    more = f" (showing the strongest {shown})" if total > shown else ""
    return (
        f"Found {total} photo{'s' if total != 1 else ''} — {label}{more}. "
        f"Each tile shows its scores so you can check my work.",
        _refs(results),
    )
