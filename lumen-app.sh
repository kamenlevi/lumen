#!/usr/bin/env bash
# Launch the built Lumen desktop app (self-contained release binary).
#
# Bound to the GNOME Ctrl+Space shortcut: if Lumen isn't running this starts
# it (showing the spotlight); if it is, the single-instance plugin forwards
# here and toggles the spotlight. Either way Ctrl+Space always works — so
# closing and reopening is reliable, unlike the dev build.

set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"

# The sidecar runs from the Python venv (system python3 lacks the deps).
export LUMEN_PYTHON="$DIR/.venv/bin/python"

exec "$DIR/frontend/src-tauri/target/release/lumen" "$@"
