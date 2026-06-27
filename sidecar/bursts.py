"""Burst detection + best-of-burst ("the keeper").

Groups photos taken seconds apart that look near-identical — using the
capture time and perceptual hash already computed at index time, so it's cheap
— then scores each shot and recommends the keeper(s).

Scoring is "smart per-burst": if the burst has faces, eyes-open wins and then
the sharpest eyes/face; otherwise the sharpest, best-exposed frame wins.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from . import db

RAPID_GAP_S = 3.5  # shots this close in time ARE the same burst (rapid fire)
TIME_GAP_S = 6.0   # up to here, group only if also visually similar
PHASH_MAX = 14     # perceptual-hash Hamming distance for "visually similar"
MIN_BURST = 2
# A shot whose score is within this fraction of the burst's best is ALSO a
# keeper — so a burst with two (or more) equally-good frames highlights them all
# instead of arbitrarily picking one.
KEEPER_TOL = 0.04


def _phash_hamming(a: str | None, b: str | None) -> int:
    if not a or not b or len(a) != len(b):
        return 999
    try:
        return bin(int(a, 16) ^ int(b, 16)).count("1")
    except ValueError:
        return 999


def _epoch(taken_at: str | None) -> float | None:
    if not taken_at:
        return None
    try:
        return datetime.fromisoformat(taken_at).timestamp()
    except ValueError:
        return None


def _same_burst(a: sqlite3.Row, b: sqlite3.Row) -> bool:
    if a["folder_id"] != b["folder_id"]:
        return False
    ta, tb = _epoch(a["taken_at"]), _epoch(b["taken_at"])
    if ta is not None and tb is not None:
        gap = abs(tb - ta)
        if gap > TIME_GAP_S:
            return False
        if gap <= RAPID_GAP_S:
            return True  # rapid succession is a burst even if the framing shifted
        return _phash_hamming(a["phash"], b["phash"]) <= PHASH_MAX
    # No timestamps → fall back to visual similarity + adjacency.
    return _phash_hamming(a["phash"], b["phash"]) <= PHASH_MAX


def _score(q: sqlite3.Row | None) -> float:
    """Higher = better keeper. Smart per-burst: people → eyes; else → sharpness."""
    if q is None:
        return 0.0
    if (q["num_faces"] or 0) > 0:
        s = 0.0
        if q["eyes_closed"]:
            s -= 1_000_000          # a blink is disqualifying vs an eyes-open sibling
        s += (q["eye_sharp"] or 0) * 2.0 + (q["face_sharp"] or 0)
        return s
    s = float(q["sharpness"] or 0)
    if q["is_dark"] or q["is_bright"]:
        s -= 500.0
    return s


def find_bursts(conn: sqlite3.Connection, limit_bursts: int | None = None) -> list[list[dict]]:
    """Return bursts (≥2 near-identical consecutive shots). Each burst is a list
    of {id, score, is_keeper}, the keeper being the highest-scoring shot."""
    rows = conn.execute(
        """SELECT i.id, i.folder_id, i.taken_at, i.phash,
                  q.num_faces, q.eyes_closed, q.eye_sharp, q.face_sharp,
                  q.sharpness, q.is_dark, q.is_bright
             FROM images i
        LEFT JOIN quality_metrics q ON q.image_id = i.id
         ORDER BY i.folder_id, COALESCE(i.taken_at, ''), i.id"""
    ).fetchall()

    groups: list[list[sqlite3.Row]] = []
    cur: list[sqlite3.Row] = []
    prev: sqlite3.Row | None = None
    for r in rows:
        if prev is not None and _same_burst(prev, r):
            cur.append(r)
        else:
            if len(cur) >= MIN_BURST:
                groups.append(cur)
            cur = [r]
        prev = r
    if len(cur) >= MIN_BURST:
        groups.append(cur)

    out: list[list[dict]] = []
    for g in groups:
        scored = [(r["id"], _score(r)) for r in g]
        top = max(sc for _, sc in scored)
        # The best frame is always a keeper; any sibling within KEEPER_TOL of it
        # (only meaningful when the top score is positive) is a keeper too.
        cutoff = top * (1.0 - KEEPER_TOL) if top > 0 else top
        out.append([
            {"id": iid, "score": round(sc, 2), "is_keeper": sc >= cutoff}
            for iid, sc in scored
        ])
    # biggest bursts first (most to cull)
    out.sort(key=len, reverse=True)
    return out[:limit_bursts] if limit_bursts else out
