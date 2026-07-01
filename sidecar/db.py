"""SQLite schema + sqlite-vec initialization."""

from __future__ import annotations

import sqlite3
import struct
from pathlib import Path
from typing import Iterable

import sqlite_vec

from .paths import db_path

SCHEMA = """
CREATE TABLE IF NOT EXISTS folders (
    id INTEGER PRIMARY KEY,
    path TEXT UNIQUE NOT NULL,
    added_at REAL NOT NULL,
    watch INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS images (
    id INTEGER PRIMARY KEY,
    path TEXT UNIQUE NOT NULL,
    folder_id INTEGER REFERENCES folders(id) ON DELETE CASCADE,
    mtime REAL NOT NULL,
    w INTEGER,
    h INTEGER,
    taken_at TEXT,
    camera TEXT,
    lat REAL,
    lon REAL,
    phash TEXT,
    sha256 TEXT,
    thumb_path TEXT,
    indexed_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS images_folder_idx ON images(folder_id);
CREATE INDEX IF NOT EXISTS images_phash_idx ON images(phash);
CREATE INDEX IF NOT EXISTS images_taken_idx ON images(taken_at);
CREATE INDEX IF NOT EXISTS images_camera_idx ON images(camera);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Tier-2 classical-CV quality metrics, computed once per image and cached.
-- One row per image; cheap to recompute, never needs a model.
CREATE TABLE IF NOT EXISTS quality_metrics (
    image_id INTEGER PRIMARY KEY REFERENCES images(id) ON DELETE CASCADE,
    sharpness REAL,               -- global Laplacian variance (normalized 1024px)
    brightness REAL,              -- mean luma, 0-255
    clip_low REAL,                -- fraction of near-black pixels (crushed shadows)
    clip_high REAL,               -- fraction of near-white pixels (blown highlights)
    subject_source TEXT,          -- 'face' | 'center' | 'none'
    subject_sharpness REAL,
    background_sharpness REAL,
    focus_ratio REAL,             -- subject_sharpness / background_sharpness
    fnumber REAL,                 -- EXIF aperture (f-number), nullable
    is_blurry INTEGER NOT NULL DEFAULT 0,
    is_dark INTEGER NOT NULL DEFAULT 0,
    is_bright INTEGER NOT NULL DEFAULT 0,
    subject_out_of_focus INTEGER NOT NULL DEFAULT 0,
    -- MediaPipe face/eye analysis (Tier-2b)
    num_faces INTEGER,
    eyes_closed INTEGER,        -- any face has a shut eye
    eyes_open_all INTEGER,      -- faces present and all eyes open
    eye_sharp REAL,             -- sharpness measured on the eye region
    face_sharp REAL,            -- sharpness measured on the main face
    subject_label TEXT,         -- main detected object (person/car/dog/…)
    subject_obj_sharp REAL,     -- sharpness measured ON that subject (not the frame)
    objects TEXT,               -- all detected object labels, comma-separated
    analyzed_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS qm_blurry_idx ON quality_metrics(is_blurry);
CREATE INDEX IF NOT EXISTS qm_dark_idx ON quality_metrics(is_dark);
CREATE INDEX IF NOT EXISTS qm_oof_idx ON quality_metrics(subject_out_of_focus);

-- Chat: multi-turn conversations with memory that persists across sessions.
-- Unlike one-shot search, a chat is a thread of messages; assistant messages
-- can carry photo results (stored as ordered id+score refs, hydrated live on
-- read so deleted photos drop out).
CREATE TABLE IF NOT EXISTS chats (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL DEFAULT 'New chat',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY,
    chat_id INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    role TEXT NOT NULL,            -- 'user' | 'assistant'
    content TEXT NOT NULL,
    results_json TEXT,            -- JSON: [{"id": int, "score": float}, ...] or NULL
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS chat_messages_chat_idx ON chat_messages(chat_id, id);

-- Tier-2 color analysis: dominant-hue histogram per image, computed once from
-- the cached thumbnail. Lets "find pink / blue / green photos" be an instant
-- filter — no model needed (CLIP is bad at color; this isn't).
CREATE TABLE IF NOT EXISTS color_metrics (
    image_id INTEGER PRIMARY KEY REFERENCES images(id) ON DELETE CASCADE,
    color_hist TEXT,      -- JSON: 12 floats, fraction of pixels per 30° hue bin
    colorfulness REAL,    -- fraction of pixels that are colorful (vs grey/dark)
    dominant_hex TEXT,    -- representative color, for a swatch in the UI
    analyzed_at REAL NOT NULL
);

-- Tier-3 VLM cards: a vision-model description per image, computed ONCE
-- (lazily, on view / idle) and cached forever so the model never re-looks.
CREATE TABLE IF NOT EXISTS vlm_cards (
    image_id INTEGER PRIMARY KEY REFERENCES images(id) ON DELETE CASCADE,
    description TEXT,
    model TEXT,
    analyzed_at REAL NOT NULL
);
"""


def _vec_table_sql(dim: int) -> str:
    return f"""
    CREATE VIRTUAL TABLE IF NOT EXISTS image_vecs USING vec0(
        id INTEGER PRIMARY KEY,
        embedding float[{dim}]
    );
    """


# Paths whose schema/migration already ran this process. connect() is called
# on every request; re-running DDL each time is wasted work, so it happens
# once per DB file. (WAL is persistent, so setting it once is also enough.)
_initialized: set[str] = set()


def connect(path: Path | None = None) -> sqlite3.Connection:
    p = path or db_path()
    conn = sqlite3.connect(str(p), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.execute("PRAGMA foreign_keys = ON")
    if str(p) not in _initialized:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        _migrate(conn)
        # If a previous run recorded the embedding size, create the vector
        # table now — then no endpoint ever needs the CLIP model loaded just
        # to touch the DB.
        dim = get_setting(conn, "embedding_dim")
        if dim:
            conn.execute(_vec_table_sql(int(dim)))
        conn.commit()
        _initialized.add(str(p))
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns introduced after the table already existed in the wild."""
    cols = [
        ("num_faces", "INTEGER"), ("eyes_closed", "INTEGER"),
        ("eyes_open_all", "INTEGER"), ("eye_sharp", "REAL"), ("face_sharp", "REAL"),
        ("subject_label", "TEXT"), ("subject_obj_sharp", "REAL"),
        ("objects", "TEXT"),
    ]
    existing = {r["name"] for r in conn.execute("PRAGMA table_info(quality_metrics)")}
    for name, typ in cols:
        if name not in existing:
            conn.execute(f"ALTER TABLE quality_metrics ADD COLUMN {name} {typ}")

    # images.sha256: exact-content hash, added after launch. The column lives
    # here (not in SCHEMA) because the index must be created only AFTER the
    # column exists — fresh DBs already have the column from CREATE TABLE, so
    # the index creation is unconditional while the ALTER is guarded.
    img_cols = {r["name"] for r in conn.execute("PRAGMA table_info(images)")}
    if "sha256" not in img_cols:
        conn.execute("ALTER TABLE images ADD COLUMN sha256 TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS images_sha256_idx ON images(sha256)")


def init_db(conn: sqlite3.Connection, embedding_dim: int) -> None:
    """Ensure the vector table exists for this embedding size and remember the
    size, so later connections can recreate the table without the model."""
    conn.execute(_vec_table_sql(embedding_dim))
    if get_setting(conn, "embedding_dim") != str(embedding_dim):
        set_setting(conn, "embedding_dim", str(embedding_dim))
    conn.commit()


def get_setting(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()


def pack_embedding(values: Iterable[float]) -> bytes:
    arr = list(values)
    return struct.pack(f"{len(arr)}f", *arr)


QUALITY_COLS = (
    "sharpness", "brightness", "clip_low", "clip_high", "subject_source",
    "subject_sharpness", "background_sharpness", "focus_ratio", "fnumber",
    "is_blurry", "is_dark", "is_bright", "subject_out_of_focus",
    "num_faces", "eyes_closed", "eyes_open_all", "eye_sharp", "face_sharp",
    "subject_label", "subject_obj_sharp", "objects",
    "analyzed_at",
)


def upsert_quality(conn: sqlite3.Connection, image_id: int, metrics: dict) -> None:
    """Insert or replace the quality_metrics row for an image. `metrics` keys
    are a subset of QUALITY_COLS; missing keys default to NULL/0."""
    cols = ["image_id", *QUALITY_COLS]
    vals = [image_id, *(metrics.get(c) for c in QUALITY_COLS)]
    placeholders = ",".join("?" * len(cols))
    conn.execute(
        f"INSERT INTO quality_metrics({','.join(cols)}) VALUES({placeholders}) "
        f"ON CONFLICT(image_id) DO UPDATE SET "
        + ",".join(f"{c}=excluded.{c}" for c in QUALITY_COLS),
        vals,
    )


def get_quality(conn: sqlite3.Connection, image_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM quality_metrics WHERE image_id = ?", (image_id,)
    ).fetchone()


def find_duplicate_groups(conn: sqlite3.Connection) -> list[list[sqlite3.Row]]:
    """Group images whose file content is byte-identical (same SHA-256).
    Returns one list per duplicated hash, newest path first; singletons are
    excluded. Lets the UI say 'this exact file exists 3 times'."""
    dup_hashes = [
        r["sha256"]
        for r in conn.execute(
            """SELECT sha256 FROM images
                WHERE sha256 IS NOT NULL AND sha256 != ''
             GROUP BY sha256 HAVING COUNT(*) > 1"""
        )
    ]
    groups: list[list[sqlite3.Row]] = []
    for h in dup_hashes:
        rows = conn.execute(
            """SELECT id, path, thumb_path, w, h, taken_at, indexed_at, sha256
                 FROM images WHERE sha256 = ? ORDER BY indexed_at DESC""",
            (h,),
        ).fetchall()
        groups.append(rows)
    return groups


def get_vlm_card(conn: sqlite3.Connection, image_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM vlm_cards WHERE image_id = ?", (image_id,)
    ).fetchone()


def upsert_vlm_card(conn: sqlite3.Connection, image_id: int, description: str, model: str) -> None:
    import time as _t
    conn.execute(
        """INSERT INTO vlm_cards(image_id, description, model, analyzed_at)
           VALUES(?,?,?,?)
           ON CONFLICT(image_id) DO UPDATE SET
             description=excluded.description, model=excluded.model,
             analyzed_at=excluded.analyzed_at""",
        (image_id, description, model, _t.time()),
    )
    conn.commit()


def count_vlm_cards(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) AS n FROM vlm_cards").fetchone()["n"]


def upsert_color(conn: sqlite3.Connection, image_id: int, m: dict) -> None:
    import time as _t
    conn.execute(
        """INSERT INTO color_metrics(image_id, color_hist, colorfulness,
                                     dominant_hex, analyzed_at)
           VALUES(?,?,?,?,?)
           ON CONFLICT(image_id) DO UPDATE SET
             color_hist=excluded.color_hist,
             colorfulness=excluded.colorfulness,
             dominant_hex=excluded.dominant_hex,
             analyzed_at=excluded.analyzed_at""",
        (image_id, m.get("color_hist"), m.get("colorfulness"),
         m.get("dominant_hex"), _t.time()),
    )


# ---------- chat ----------

import json as _json
import time as _time


def create_chat(conn: sqlite3.Connection, title: str = "New chat") -> int:
    now = _time.time()
    cur = conn.execute(
        "INSERT INTO chats(title, created_at, updated_at) VALUES(?, ?, ?)",
        (title, now, now),
    )
    conn.commit()
    return cur.lastrowid


def list_chats(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT c.id, c.title, c.created_at, c.updated_at,
                  COUNT(m.id) AS message_count
             FROM chats c
        LEFT JOIN chat_messages m ON m.chat_id = c.id
         GROUP BY c.id
         ORDER BY c.updated_at DESC"""
    ).fetchall()


def get_chat(conn: sqlite3.Connection, chat_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM chats WHERE id = ?", (chat_id,)).fetchone()


def rename_chat(conn: sqlite3.Connection, chat_id: int, title: str) -> None:
    conn.execute(
        "UPDATE chats SET title = ?, updated_at = ? WHERE id = ?",
        (title, _time.time(), chat_id),
    )
    conn.commit()


def delete_chat(conn: sqlite3.Connection, chat_id: int) -> None:
    # chat_messages cascade via FK (foreign_keys pragma is ON in connect()).
    conn.execute("DELETE FROM chats WHERE id = ?", (chat_id,))
    conn.commit()


def add_message(
    conn: sqlite3.Connection,
    chat_id: int,
    role: str,
    content: str,
    results: list[dict] | None = None,
) -> int:
    now = _time.time()
    cur = conn.execute(
        """INSERT INTO chat_messages(chat_id, role, content, results_json, created_at)
           VALUES(?, ?, ?, ?, ?)""",
        (chat_id, role, content, _json.dumps(results) if results else None, now),
    )
    conn.execute("UPDATE chats SET updated_at = ? WHERE id = ?", (now, chat_id))
    conn.commit()
    return cur.lastrowid


def get_messages(conn: sqlite3.Connection, chat_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM chat_messages WHERE chat_id = ? ORDER BY id ASC", (chat_id,)
    ).fetchall()


def hydrate_results(conn: sqlite3.Connection, results_json: str | None) -> list[dict]:
    """Turn stored [{"id","score"}] refs into full result dicts for display,
    preserving order and silently dropping photos that no longer exist."""
    if not results_json:
        return []
    refs = _json.loads(results_json)
    out: list[dict] = []
    for ref in refs:
        row = conn.execute(
            """SELECT images.id, images.path, images.thumb_path, images.w, images.h,
                      images.taken_at, images.camera, images.lat, images.lon,
                      q.sharpness, q.is_blurry, q.is_dark, q.is_bright,
                      q.subject_out_of_focus, cm.dominant_hex
                 FROM images
            LEFT JOIN quality_metrics q ON q.image_id = images.id
            LEFT JOIN color_metrics cm ON cm.image_id = images.id
                WHERE images.id = ?""",
            (ref["id"],),
        ).fetchone()
        if row:
            d = dict(row)
            d["score"] = ref.get("score", 0.0)
            if "keeper" in ref:
                d["keeper"] = ref["keeper"]
            out.append(d)
    return out
