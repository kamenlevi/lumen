"""Chat brain: turn a user message (in the context of a conversation) into an
assistant reply plus any photo results to show.

This is deliberately a thin, swappable seam. Right now it does the one thing we
already have end-to-end — semantic photo search — and phrases a short reply.
Later steps replace `respond()` with a real agent that can also call the Tier-2
quality metrics and a Tier-3 vision model, and reason over multiple turns. The
interface (conversation history in, text + result-refs out) stays the same, so
the UI and storage never have to change.
"""

from __future__ import annotations

import sqlite3

from .search import search_text

# How many photos a search-style answer shows in the results gallery.
DEFAULT_RESULT_K = 24


def _title_from(text: str) -> str:
    t = " ".join(text.strip().split())
    return (t[:40] + "…") if len(t) > 40 else t or "New chat"


def respond(
    conn: sqlite3.Connection,
    history: list[sqlite3.Row],
    user_text: str,
) -> tuple[str, list[dict]]:
    """Return (assistant_text, result_refs). `result_refs` is a list of
    {"id", "score"} dicts to persist and hydrate for the gallery.

    `history` is the prior messages in this chat (oldest first), available for
    future multi-turn reasoning. v1 answers each turn as a fresh search.
    """
    query = user_text.strip()
    if not query:
        return ("What would you like to find or know about your photos?", [])

    results = search_text(query, top_k=DEFAULT_RESULT_K)
    refs = [{"id": r.id, "score": round(r.score, 4)} for r in results]

    if not refs:
        text = (
            f'I couldn’t find anything matching “{query}”. '
            "Make sure the folder is indexed in the Library tab, or try "
            "describing the photo a different way."
        )
    else:
        text = f'Here are {len(refs)} photos matching “{query}”.'
    return (text, refs)
