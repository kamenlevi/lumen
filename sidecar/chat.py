"""Chat brain: turn a user message into an assistant reply plus photo results.

This is a deliberately thin, swappable seam. It is NOT yet a full LLM agent
(that's a later step). All finding logic lives in query_engine — the ONE brain
shared with the /search endpoint — so chat and plain search always agree.
Chat adds: conversation-only intents (exact duplicates, bursts) and the prose
around the results.
"""

from __future__ import annotations

import re
import sqlite3

from . import bursts as bursts_mod
from . import db
from . import query_engine as engine
from .query_engine import DEFAULT_RESULT_K, QueryAnswer
from .search import SearchResult


def _title_from(text: str) -> str:
    t = " ".join(text.strip().split())
    return (t[:40] + "…") if len(t) > 40 else t or "New chat"


def _refs(results: list[SearchResult]) -> list[dict]:
    return [{"id": r.id, "score": round(r.score, 4)} for r in results]


_EMPTY_LIBRARY_MSG = (
    "Your library is empty — there's nothing for me to search yet. "
    "Open the Library tab, add a photo folder, and hit Index. "
    "After that you can ask me anything about your photos."
)


def respond(
    conn: sqlite3.Connection,
    history: list[sqlite3.Row],
    user_text: str,
) -> tuple[str, list[dict]]:
    query = user_text.strip()
    if not query:
        return ("What would you like to find or know about your photos?", [])

    # Guide a brand-new user instead of pretending to search nothing.
    if not conn.execute("SELECT 1 FROM images LIMIT 1").fetchone():
        return (_EMPTY_LIBRARY_MSG, [])

    # Exact duplicates (byte-identical files) — distinct from bursts, which are
    # near-identical *different* frames. Route explicit "same/identical file"
    # language here; generic "duplicate" still means burst/cull below.
    if re.search(r"exact (duplicate|cop)|duplicate file|same file|identical (file|cop|photo|image)|"
                 r"byte[- ]identical|same exact|copies of the same", query.lower()):
        return _answer_exact_duplicates(conn)

    if re.search(r"\bburst|keeper|best (shot|photo|one|of)|near[- ]identical|duplicate|"
                 r"pick the best|which (one )?is best|similar shots|cull\b", query.lower()):
        return _answer_bursts(conn)

    ans = engine.run_query(conn, query, top_k=DEFAULT_RESULT_K)

    if ans.missing_data == "quality":
        return (
            "I haven't analysed your library for image quality yet (sharpness, "
            "exposure, focus). Once that pass has run I can answer this — for now "
            "I can only do content search.",
            [],
        )
    if ans.missing_data == "color":
        return (
            "I haven't analysed your library's colors yet. Once that quick pass "
            "has run, “find pink photos” becomes an instant filter.",
            [],
        )

    if ans.kind == "quality":
        return _prose_quality(conn, ans)
    if ans.kind == "color":
        return _prose_color(ans)
    return _prose_search(query, ans)


def _answer_exact_duplicates(conn: sqlite3.Connection) -> tuple[str, list[dict]]:
    groups = db.find_duplicate_groups(conn)
    if not groups:
        return (
            "No exact duplicates — every indexed photo has unique file content "
            "(matched by SHA-256). If you expected some, re-index so older photos "
            "get a content hash first.",
            [],
        )
    refs: list[dict] = []
    for grp in groups:
        # First copy (newest) is the one to keep; the rest are redundant.
        for i, r in enumerate(grp):
            refs.append({"id": r["id"], "score": 1.0 - i * 0.001, "keeper": i == 0})
    extra = sum(len(g) - 1 for g in groups)
    return (
        f"Found {len(groups)} set{'s' if len(groups) != 1 else ''} of exact "
        f"duplicates — {extra} redundant cop{'ies' if extra != 1 else 'y'} you "
        f"could delete to reclaim space. One file per set is starred ★ to keep; "
        f"the others are byte-for-byte identical.",
        refs,
    )


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


def _prose_quality(conn: sqlite3.Connection, ans: QueryAnswer) -> tuple[str, list[dict]]:
    label = ans.quality_label
    if ans.content:
        if not ans.results:
            return (f"None of the photos matching “{ans.content}” have {label}.", [])
        return (
            f"Of your photos matching “{ans.content}” — {len(ans.results)} with {label}.",
            _refs(ans.results),
        )
    if not ans.results:
        return (f"No photos found with {label}.", [])
    shown = len(ans.results)
    total = ans.total_matching or shown
    more = f" (showing the strongest {shown})" if total > shown else ""
    return (
        f"Found {total} photo{'s' if total != 1 else ''} — {label}{more}. "
        f"Each tile shows its scores so you can check my work.",
        _refs(ans.results),
    )


def _prose_color(ans: QueryAnswer) -> tuple[str, list[dict]]:
    color = ans.color
    if ans.content:
        if not ans.results:
            return (f"None of your “{ans.content}” photos are noticeably {color}.", [])
        return (
            f"Your {color} photos matching “{ans.content}” — {len(ans.results)}, "
            f"most {color} first.",
            _refs(ans.results),
        )
    if not ans.results:
        return (f"I didn’t find noticeably {color} photos in your library.", [])
    return (
        f"Here are your most {color} photos ({len(ans.results)} shown, "
        f"strongest first). This uses each photo’s color makeup, not keywords.",
        _refs(ans.results),
    )


def _prose_search(query: str, ans: QueryAnswer) -> tuple[str, list[dict]]:
    if not ans.results:
        return (
            f"I couldn’t find anything matching “{query}”. Make sure the folder "
            "is indexed in the Library tab, or try describing it differently.",
            [],
        )
    bits = []
    if ans.object_hits:
        bits.append(f"{ans.object_hits} where I detected a {ans.detected_object}")
    if ans.description_hits:
        bits.append(f"{ans.description_hits} from AI descriptions")
    note = (" — " + ", ".join(bits)) if bits else ""
    return (f"Here are {len(ans.results)} photos matching “{query}”{note}.", _refs(ans.results))
