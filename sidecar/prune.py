"""Drop rows from the DB when their files no longer exist on disk.

CLI:
    python -m sidecar.prune              # all folders
    python -m sidecar.prune --folder /x  # one folder
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import db
from .thumb import delete_artifacts


def _delete_rows(conn, rows: list) -> int:
    """Drop image rows (given as sqlite Rows with id/path/thumb_path) plus
    their vectors and cached thumbnail/preview files."""
    if not rows:
        return 0
    ids = [r["id"] for r in rows]
    qs = ",".join("?" * len(ids))
    conn.execute(f"DELETE FROM image_vecs WHERE id IN ({qs})", ids)
    conn.execute(f"DELETE FROM images WHERE id IN ({qs})", ids)
    conn.commit()
    for r in rows:
        delete_artifacts(r["path"], r["thumb_path"])
    return len(ids)


def prune_paths(paths: list[str]) -> int:
    """Drop rows for an exact list of paths."""
    if not paths:
        return 0
    conn = db.connect()
    qs = ",".join("?" * len(paths))
    rows = conn.execute(
        f"SELECT id, path, thumb_path FROM images WHERE path IN ({qs})", paths
    ).fetchall()
    return _delete_rows(conn, rows)


def prune_folder(folder_id: int) -> int:
    """Drop rows in `folder_id` whose files don't exist on disk anymore."""
    conn = db.connect()
    rows = conn.execute(
        "SELECT id, path, thumb_path FROM images WHERE folder_id = ?", (folder_id,)
    ).fetchall()
    dead = [r for r in rows if not Path(r["path"]).exists()]
    return _delete_rows(conn, dead)


def prune_all() -> int:
    """Drop every image row whose file is gone."""
    conn = db.connect()
    rows = conn.execute("SELECT id, path, thumb_path FROM images").fetchall()
    dead = [r for r in rows if not Path(r["path"]).exists()]
    return _delete_rows(conn, dead)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Drop rows for files that no longer exist.")
    p.add_argument("--folder", default=None, help="Limit to one indexed folder path.")
    args = p.parse_args(argv)

    if args.folder:
        conn = db.connect()
        row = conn.execute("SELECT id FROM folders WHERE path = ?", (args.folder,)).fetchone()
        if not row:
            print(f"Folder not tracked: {args.folder}", file=sys.stderr)
            return 2
        n = prune_folder(row["id"])
    else:
        n = prune_all()
    print(f"Pruned {n} dead rows.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
