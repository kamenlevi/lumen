"""Model management for Lumen's VLM tasks.

Lists and selects models for the vision tier: local ones via Ollama (the
on-device, fits-any-CPU path) and cloud ones via OpenRouter (optional, faster/
better, costs money). Includes a hardware-aware recommendation and one-click
local download. Ideas mirror the model menu in kamenlevi/excel-ai-assistant.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import urllib.request

from . import db

OLLAMA = "http://localhost:11434"

# Curated on-device vision models (pulled via Ollama), CPU-friendliest first.
RECOMMENDED_LOCAL = [
    {"name": "moondream", "size_gb": 1.7, "min_ram_gb": 4,
     "blurb": "Tiny vision model — fast on any CPU. Best on-device default."},
    {"name": "llava:7b", "size_gb": 4.7, "min_ram_gb": 8,
     "blurb": "Larger and more accurate. ~8 GB RAM; slower on CPU."},
    {"name": "llama3.2-vision", "size_gb": 7.8, "min_ram_gb": 16,
     "blurb": "High quality but heavy — best with a GPU or 16 GB+ RAM."},
]

_VISION_HINTS = ("llava", "moondream", "vision", "bakllava", "qwen2-vl",
                 "qwen2.5vl", "minicpm-v", "granite", "gemma3")


def _http(url: str, data: dict | None = None, timeout: float = 4):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(
        url, data=body, method="POST" if data is not None else "GET",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def hardware_info() -> dict:
    cores = os.cpu_count() or 1
    total = avail = None
    try:
        mi: dict[str, str] = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, _, v = line.partition(":")
                mi[k] = v.strip()
        total = round(int(mi["MemTotal"].split()[0]) / 1_048_576, 1)
        avail = round(int(mi["MemAvailable"].split()[0]) / 1_048_576, 1)
    except Exception:
        pass
    gpu = None
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=2,
        )
        if out.returncode == 0 and out.stdout.strip():
            gpu = out.stdout.strip().splitlines()[0]
    except Exception:
        pass
    return {"cores": cores, "ram_total_gb": total, "ram_available_gb": avail, "gpu": gpu}


def ollama_up() -> bool:
    try:
        _http(f"{OLLAMA}/api/tags", timeout=2)
        return True
    except Exception:
        return False


def _is_vision(name: str) -> bool:
    n = name.lower()
    return any(h in n for h in _VISION_HINTS)


def installed_models() -> list[dict]:
    try:
        data = _http(f"{OLLAMA}/api/tags", timeout=3)
    except Exception:
        return []
    return [
        {"name": m["name"], "size_gb": round(m.get("size", 0) / 1e9, 1),
         "vision": _is_vision(m["name"]), "installed": True}
        for m in data.get("models", [])
    ]


def recommended(hw: dict, installed: list[dict]) -> list[dict]:
    have = {m["name"] for m in installed}
    ram = hw.get("ram_total_gb") or 8
    out = []
    for m in RECOMMENDED_LOCAL:
        out.append({**m, "vision": True, "installed": m["name"] in have,
                    "fits_ram": m["min_ram_gb"] <= ram})
    return out


def recommendation(hw: dict, installed: list[dict]) -> dict:
    for m in installed:
        if m["vision"]:
            return {"provider": "ollama", "model": m["name"],
                    "why": f"{m['name']} is already installed and can see images."}
    ram = hw.get("ram_total_gb") or 8
    pick = RECOMMENDED_LOCAL[0]  # moondream — smallest, runs anywhere
    return {"provider": "ollama", "model": pick["name"],
            "why": (f"On {hw.get('cores')} cores / {ram} GB RAM and no GPU, "
                    f"{pick['name']} runs fully on-device and fast. Download it "
                    "below, or pick a cloud model for top quality.")}


# ---- one-click download (Ollama pull) with progress ----
_pull = {"name": None, "status": "idle", "percent": 0, "error": None}
_pull_lock = threading.Lock()


def pull_status() -> dict:
    with _pull_lock:
        return dict(_pull)


def start_pull(name: str) -> None:
    with _pull_lock:
        if _pull["status"] == "pulling":
            return
        _pull.update(name=name, status="pulling", percent=0, error=None)

    def run() -> None:
        try:
            req = urllib.request.Request(
                f"{OLLAMA}/api/pull",
                data=json.dumps({"name": name, "stream": True}).encode(),
                headers={"Content-Type": "application/json"}, method="POST",
            )
            with urllib.request.urlopen(req, timeout=3600) as r:
                for raw in r:
                    if not raw.strip():
                        continue
                    try:
                        ev = json.loads(raw.decode())
                    except Exception:
                        continue
                    total, done = ev.get("total"), ev.get("completed")
                    if total and done:
                        with _pull_lock:
                            _pull["percent"] = round(100 * done / total)
                    if ev.get("status") == "success":
                        with _pull_lock:
                            _pull.update(status="done", percent=100)
            with _pull_lock:
                if _pull["status"] != "done":
                    _pull.update(status="done", percent=100)
        except Exception as e:
            with _pull_lock:
                _pull.update(status="error", error=str(e))

    threading.Thread(target=run, daemon=True).start()


# ---- cloud catalog (OpenRouter, vision-capable, with prices) ----
_catalog = {"at": 0.0, "data": None}


def cloud_catalog() -> list[dict]:
    if _catalog["data"] and time.time() - _catalog["at"] < 3600:
        return _catalog["data"]
    try:
        data = _http("https://openrouter.ai/api/v1/models", timeout=8)
    except Exception:
        return []
    out = []
    for m in data.get("data", []):
        arch = m.get("architecture") or {}
        mods = arch.get("input_modalities") or []
        if "image" not in mods and "image" not in (arch.get("modality") or ""):
            continue
        if "router" in m["id"].lower() or "router" in m.get("name", "").lower():
            continue  # skip OpenRouter's auto-router pseudo-models (junk pricing)
        pr = m.get("pricing") or {}
        in_price = max(0.0, round(float(pr.get("prompt", 0)) * 1e6, 2)) if pr.get("prompt") else 0
        out_price = max(0.0, round(float(pr.get("completion", 0)) * 1e6, 2)) if pr.get("completion") else 0
        out.append({
            "id": m["id"], "name": m.get("name", m["id"]),
            "in": in_price, "out": out_price,
        })
    out.sort(key=lambda x: (x["in"], x["name"].lower()))
    _catalog.update(at=time.time(), data=out)
    return out


# ---- selection (persisted) ----
def get_selection(conn) -> dict:
    return {
        "provider": db.get_setting(conn, "vlm_provider", "ollama"),
        "model": db.get_setting(conn, "vlm_model", ""),
    }


def set_selection(conn, provider: str, model: str) -> None:
    db.set_setting(conn, "vlm_provider", provider)
    db.set_setting(conn, "vlm_model", model)
