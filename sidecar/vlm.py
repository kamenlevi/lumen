"""Tier-3 vision model: an optional, on-demand "look closely" for questions
classic CV and CLIP can't answer (scene, objects, text, mood).

Conditional by design: it only runs if the user has selected a model in the
Models tab, and only when asked. Each result is cached in vlm_cards so the
model never re-analyzes the same photo. Generation is ASYNC — on a CPU a local
model can take a couple of minutes per image, so we never block on it; the UI
polls for the result and the background daemon fills the rest when idle.
"""

from __future__ import annotations

import base64
import json
import threading
import urllib.request
from pathlib import Path

from . import db

OLLAMA = "http://localhost:11434"

DESCRIBE_PROMPT = (
    "Describe this photo in 2-3 sentences: the main subject(s), the setting, "
    "notable colors, the mood, and any visible text. Be concrete and concise."
)
MAX_TOKENS = 160

# image_id -> {"status": "generating"|"done"|"error", ...}
_jobs: dict[int, dict] = {}
_lock = threading.Lock()


def selected_model(conn) -> tuple[str, str]:
    return (
        db.get_setting(conn, "vlm_provider", "ollama") or "ollama",
        db.get_setting(conn, "vlm_model", "") or "",
    )


def available(conn) -> bool:
    provider, model = selected_model(conn)
    if not model:
        return False
    if provider == "ollama":
        try:
            urllib.request.urlopen(f"{OLLAMA}/api/tags", timeout=2)
            return True
        except Exception:
            return False
    if provider == "openrouter":
        return bool(db.get_setting(conn, "openrouter_api_key", ""))
    return False


# ---- provider calls ----

def _ollama_describe(model: str, image_path: str, prompt: str) -> str:
    b64 = base64.b64encode(Path(image_path).read_bytes()).decode()
    payload = {
        "model": model, "prompt": prompt, "images": [b64], "stream": False,
        "options": {"num_predict": MAX_TOKENS},
    }
    req = urllib.request.Request(
        f"{OLLAMA}/api/generate", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=600) as r:
        return (json.loads(r.read()).get("response") or "").strip()


def _openrouter_describe(conn, model: str, image_path: str, prompt: str) -> str:
    key = db.get_setting(conn, "openrouter_api_key", "")
    if not key:
        raise RuntimeError("No OpenRouter API key (add it in Settings).")
    b64 = base64.b64encode(Path(image_path).read_bytes()).decode()
    payload = {
        "model": model, "max_tokens": MAX_TOKENS,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
        ]}],
    }
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.loads(r.read())
    return (data["choices"][0]["message"]["content"] or "").strip()


def describe(conn, image_path: str, prompt: str = DESCRIBE_PROMPT) -> str:
    provider, model = selected_model(conn)
    if not model:
        raise RuntimeError("No vision model selected.")
    if provider == "ollama":
        return _ollama_describe(model, image_path, prompt)
    if provider == "openrouter":
        return _openrouter_describe(conn, model, image_path, prompt)
    raise RuntimeError(f"Unknown provider: {provider}")


# ---- async card generation (cached) ----

def card_status(conn, image_id: int) -> dict:
    c = db.get_vlm_card(conn, image_id)
    if c:
        return {"status": "done", "description": c["description"], "model": c["model"], "cached": True}
    with _lock:
        j = _jobs.get(image_id)
    return j or {"status": "none"}


def request_card(conn, image_id: int, force: bool = False) -> dict:
    """Return a cached card, or kick off async generation and return 'generating'."""
    if not force:
        c = db.get_vlm_card(conn, image_id)
        if c:
            return {"status": "done", "description": c["description"], "model": c["model"], "cached": True}
    with _lock:
        if _jobs.get(image_id, {}).get("status") == "generating":
            return _jobs[image_id]
        _jobs[image_id] = {"status": "generating"}
    threading.Thread(target=_generate, args=(image_id,), daemon=True).start()
    return {"status": "generating"}


def _generate(image_id: int) -> None:
    try:
        conn = db.connect()
        row = conn.execute(
            "SELECT path, thumb_path FROM images WHERE id = ?", (image_id,)
        ).fetchone()
        if not row:
            raise RuntimeError("image not found")
        _, model = selected_model(conn)
        # The thumbnail is plenty for a description and keeps the model fast.
        img = row["thumb_path"] or row["path"]
        desc = describe(conn, img)
        db.upsert_vlm_card(conn, image_id, desc, model)
        with _lock:
            _jobs[image_id] = {"status": "done", "description": desc, "model": model}
    except Exception as e:
        with _lock:
            _jobs[image_id] = {"status": "error", "error": str(e)}
