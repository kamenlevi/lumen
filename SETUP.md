# Setup & moving Lumen to another machine

Everything that matters lives in **two places**: this Git repo (all the code)
and a small data folder (your index + chats). Your actual photos are never
copied — Lumen just points at them where they already are.

## A. Fresh setup on any machine

```bash
git clone https://github.com/kamenlevi/lumen
cd lumen
./setup.sh            # builds the Python venv + installs frontend deps
./run.sh              # browser mode → http://127.0.0.1:5173/chat/
```

`setup.sh` needs **Python ≥3.9** (3.11 ideal — 3.8 is too old), **Node + pnpm**,
and ideally **uv**. It auto-installs the GPU build of torch if it sees an NVIDIA
card, otherwise the smaller CPU build.

## B. The native desktop app (global Ctrl+Space, tray) — Ubuntu 22.04+ only

The native shell needs `webkit2gtk-4.1`, which exists on **22.04+** but **not
20.04**. On 22.04+:

```bash
sudo apt update && sudo apt install -y \
  libwebkit2gtk-4.1-dev libxdo-dev libssl-dev \
  libayatana-appindicator3-dev librsvg2-dev build-essential

cd frontend
LUMEN_PYTHON="$PWD/../.venv/bin/python" pnpm tauri dev    # run with hot reload
# or build an installable bundle:
LUMEN_PYTHON="$PWD/../.venv/bin/python" pnpm tauri build  # → src-tauri/target/release/bundle/
```

`LUMEN_PYTHON` points the Rust shell at the sidecar venv so it doesn't fall back
to the system `python3`.

## C. Carry over your indexed photos + chat history (optional)

The index database, thumbnails, downloaded CLIP weights, and all chats live in
one portable folder. Copy it to the same path on the new machine:

```bash
# on the OLD machine
tar czf lumen-data.tgz -C ~/.local/share lumen
# move lumen-data.tgz to the new machine, then:
mkdir -p ~/.local/share && tar xzf lumen-data.tgz -C ~/.local/share
```

If the new machine mounts your photos at a **different path**, re-index there
instead (the stored absolute paths won't resolve). Search/chat are cheap; only
the initial index is slow.

## D. Continue the Claude Code project context (optional)

Our working notes for this project live in Claude's memory. To keep that
continuity on the new laptop (same username/home layout), copy:

```
~/.claude/projects/-home-kamen/memory/
```

to the same location on the new machine. That carries the project history,
decisions, and roadmap so a new Claude Code session picks up where we left off.
(The live chat transcript itself doesn't transfer — the memory files are what
preserve context.)
