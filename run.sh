#!/usr/bin/env bash
# Start the World Forge web UI.
set -e
cd "$(dirname "$0")"
PY=python3
command -v python3 >/dev/null 2>&1 || PY=python
echo "Installing dependencies (first run only)..."
$PY -m pip install -r requirements.txt
echo "Starting World Forge on http://127.0.0.1:5000"
$PY app.py
