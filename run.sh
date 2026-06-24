#!/usr/bin/env bash
# Lumen dev launcher (browser mode).
#
# Ubuntu 20.04 can't build the native Tauri shell (it needs webkit2gtk-4.1,
# which only ships on 22.04+). This runs the exact same app in your browser:
# the Python sidecar + the SvelteKit UI, talking over HTTP on a fixed port.
#
#   ./run.sh         # start sidecar + UI, print the URL
#   Ctrl+C           # stop both
#
# Global Ctrl+Space spotlight is a native-shell feature and is NOT available
# in browser mode — see the README for the Focal-native plan.

set -euo pipefail
cd "$(dirname "$0")"

SIDE_PORT=8765
UI_PORT=5173
VENV=.venv/bin/python

if [ ! -x "$VENV" ]; then
  echo "Python venv missing. Create it with:"
  echo "  uv venv --python python3.11 .venv && uv pip install --python $VENV -r sidecar/requirements.txt"
  exit 1
fi

echo "Starting Lumen sidecar on :$SIDE_PORT …"
LUMEN_PORT=$SIDE_PORT "$VENV" -m sidecar.server &
SIDE_PID=$!

echo "Starting UI on :$UI_PORT …"
( cd frontend && VITE_SIDECAR_PORT=$SIDE_PORT pnpm dev --host 127.0.0.1 --port $UI_PORT ) &
UI_PID=$!

cleanup() {
  echo; echo "Stopping Lumen…"
  kill "$SIDE_PID" "$UI_PID" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup INT TERM

sleep 3
echo
echo "──────────────────────────────────────────────"
echo "  Lumen is running.  Open:"
echo "     http://127.0.0.1:$UI_PORT/chat/"
echo "  Press Ctrl+C here to stop."
echo "──────────────────────────────────────────────"
wait
