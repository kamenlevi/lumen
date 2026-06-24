# Lumen — handoff / continuation notes

**If you're a Claude Code session picking this up on a new machine: read this
file + `SETUP.md` + `README.md`, then continue building. This is an ongoing
project with a non-technical owner (kamen) who is learning by building — teach
as you go, work step by step, and commit + push after every change.**

## What Lumen is

A local "Spotlight for photos" on Linux: find images by description **and** chat
with an assistant that inspects the actual pixels (e.g. "are all my portraits in
focus?"). Built on top of [PhotoCLIP](https://github.com/kamenlevi/photoclip)
(Tauri 2 + SvelteKit + Python FastAPI sidecar, open_clip, SQLite + sqlite-vec).

Design = **3 tiers of "looking"**: T1 CLIP semantic search (done), T2 classical
CV quality metrics (done), T3 vision-language model for open-ended judgment
(planned). A chat assistant on top uses all tiers as tools, with history.

## What's built so far

- **Step 1** — forked PhotoCLIP → Lumen, fully rebranded, working base.
- **Step 2** — `sidecar/quality.py` + `quality_metrics` table: per-image
  sharpness, exposure, and subject-vs-background focus (Haar face detection
  validated by an eye sub-cascade to kill texture false-positives; EXIF
  aperture-aware so intentional bokeh isn't flagged). Validated on 107 real DJI
  drone photos. CLI: `python -m sidecar.quality <folder>`.
- **Chat surface** (built early at user request) — `/chat` split view:
  conversation left, results gallery right, ☰ history drawer. `chats` /
  `chat_messages` tables persist across sessions; results stored as id+score
  refs, hydrated live. `sidecar/chat.py` is a **swappable brain** — does
  semantic search now; Steps 4–6 plug in Tiers 2–3 + multi-turn reasoning
  behind the same interface without touching the UI.

## How to run

- **Browser mode (any machine):** `./setup.sh` then `./run.sh` →
  http://127.0.0.1:5173/chat/
- **Native app (Ubuntu 22.04+ / 24.04, gives global Ctrl+Space + tray):** see
  `SETUP.md` §B — `pnpm tauri dev` with `LUMEN_PYTHON` set to the venv.

Env: needs **Python ≥3.9** (3.8 too old). torch + torchvision must be installed
together from the CPU index (or both CUDA) or you hit "torchvision::nms does not
exist". `setup.sh` handles this.

## Remaining roadmap

- **Step 3** — instant quality-filter chips (`Blurry` / `Dark` /
  `Out-of-focus subject`) in the UI over the cached Tier-2 metrics.
- **Step 4** — model picker (local Ollama/downloadable + cloud OpenRouter/Groq,
  hardware scan + auto-recommend, cost estimates). Port the ideas from
  `excel-ai-assistant` (`/api/models/catalog`, `/api/models/local`,
  `/api/recommend-model`, the `renderModelList` UI).
- **Step 5** — Tier-3 VLM: per-image inspection tool, local or cloud per the
  picker.
- **Step 6** — make `chat.py` a real agent that uses Tiers 1–3 as tools.

## Where we left off (2026-06-24)

Owner's main laptop is **Ubuntu 20.04**, which can't build the native app
(needs webkit2gtk-4.1, only on 22.04+). Plan: they upgrade that laptop to 22.04
in place, and meanwhile continue on their **other laptop (Ubuntu 24.04)** where
the full native app — including Ctrl+Space — builds today. Next concrete task is
**Step 3**, or wiring real intelligence into the chat brain.
