#!/usr/bin/env bash
# Lumen one-shot setup for a fresh machine.
#
#   git clone https://github.com/kamenlevi/lumen && cd lumen && ./setup.sh
#
# Sets up the Python sidecar venv and the frontend deps. Safe to re-run.
# Needs: python3.11 (or any >=3.9), node+pnpm, and `uv` (recommended).
# For the NATIVE desktop app you also need the system libs — see SETUP.md
# (only works on Ubuntu 22.04+, which has webkit2gtk-4.1).

set -euo pipefail
cd "$(dirname "$0")"

# ---- pick a Python >=3.9 ----
PY=""
for c in python3.12 python3.11 python3.10 python3; do
  if command -v "$c" >/dev/null 2>&1; then
    v=$("$c" -c 'import sys;print(sys.version_info[:2]>=(3,9))' 2>/dev/null || echo False)
    [ "$v" = "True" ] && { PY="$c"; break; }
  fi
done
[ -z "$PY" ] && { echo "Need Python >= 3.9 (3.8 is too old). Install python3.11."; exit 1; }
echo "Using Python: $($PY --version)"

# ---- venv ----
HAVE_UV=0; command -v uv >/dev/null 2>&1 && HAVE_UV=1
if [ "$HAVE_UV" = 1 ]; then
  uv venv --python "$PY" .venv
  PIP=(uv pip install --python .venv/bin/python)
else
  "$PY" -m venv .venv
  .venv/bin/pip install --upgrade pip
  PIP=(.venv/bin/pip install)
fi

# ---- torch: GPU build if an NVIDIA GPU is present, else CPU (smaller) ----
if command -v nvidia-smi >/dev/null 2>&1; then
  echo "NVIDIA GPU detected — installing CUDA torch."
  "${PIP[@]}" torch torchvision
else
  echo "No GPU — installing CPU torch (torch+torchvision must match)."
  "${PIP[@]}" torch torchvision --index-url https://download.pytorch.org/whl/cpu
fi

# ---- the rest of the sidecar deps ----
"${PIP[@]}" -r sidecar/requirements.txt

# ---- frontend ----
if command -v pnpm >/dev/null 2>&1; then
  ( cd frontend && pnpm install )
else
  echo "pnpm not found — install Node + pnpm, then run: (cd frontend && pnpm install)"
fi

# ---- optional: pre-fetch Rust crates for the native build ----
if command -v cargo >/dev/null 2>&1; then
  ( cd frontend/src-tauri && cargo fetch >/dev/null 2>&1 || true )
fi

echo
echo "Done. Quick check:"
.venv/bin/python -c "import torch,open_clip,cv2,fastapi,sqlite_vec; print('  sidecar imports OK · torch', torch.__version__)"
echo
echo "Run it (browser mode):   ./run.sh   then open http://127.0.0.1:5173/chat/"
echo "Native app (22.04+):     see SETUP.md"
