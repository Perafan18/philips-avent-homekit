#!/usr/bin/env bash
# run-bridge.sh — start the Go bridge in addon mode, reading the session JSON.
# Used by the LaunchAgent. Requires avent-login.py to have produced the JSON.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="${AVENT_CONFIG:-$ROOT/philips_avent_bridge.json}"

if [ ! -f "$CONFIG" ]; then
  echo "Missing $CONFIG — run: ./.venv/bin/python scripts/avent-login.py --email you@example.com" >&2
  exit 1
fi
exec "$ROOT/bin/avent-webrtc-bridge" addon --config "$CONFIG"
