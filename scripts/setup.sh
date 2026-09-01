#!/usr/bin/env bash
# setup.sh — fetch upstream aventproxy, build its Go bridge, and create the
# Python venv used by avent-login.py. Idempotent. Run from the repo root.
#
# Requirements: git, Go 1.24+, Python 3.11+ (macOS: `brew install go`).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

AVENTPROXY_REF="${AVENTPROXY_REF:-main}"
VENDOR="$ROOT/vendor/aventproxy"

echo "==> 1/3 Fetching upstream aventproxy ($AVENTPROXY_REF)"
if [ -d "$VENDOR/.git" ]; then
  git -C "$VENDOR" fetch --depth 1 origin "$AVENTPROXY_REF" && git -C "$VENDOR" checkout -q FETCH_HEAD
else
  git clone --depth 1 -b "$AVENTPROXY_REF" https://github.com/thekoma/aventproxy.git "$VENDOR"
fi

echo "==> Applying local patches to upstream"
# Security: default the bridge's RTSP server to loopback (upstream binds all
# interfaces, exposing the plaintext stream to the whole LAN). Idempotent.
for p in "$ROOT"/patches/*.patch; do
  [ -e "$p" ] || continue
  if git -C "$VENDOR" apply --reverse --check "$p" >/dev/null 2>&1; then
    echo "    already applied: $(basename "$p")"
  elif git -C "$VENDOR" apply --check "$p" >/dev/null 2>&1; then
    git -C "$VENDOR" apply "$p" && echo "    applied: $(basename "$p")"
  else
    echo "    WARNING: could not apply $(basename "$p") (upstream may have changed)"
  fi
done

echo "==> 2/3 Building the Go WebRTC->RTSP bridge"
command -v go >/dev/null || { echo "Go is required (brew install go)"; exit 1; }
mkdir -p "$ROOT/bin"
( cd "$VENDOR/avent-webrtc-bridge" && CGO_ENABLED=0 go build -o "$ROOT/bin/avent-webrtc-bridge" . )
echo "    built: bin/avent-webrtc-bridge"

echo "==> 3/3 Creating Python venv (aiohttp + pycryptodome)"
python3 -m venv "$ROOT/.venv"
"$ROOT/.venv/bin/pip" install --quiet --disable-pip-version-check aiohttp pycryptodome

cat <<EOF

Done. Next:
  1) Log in (writes philips_avent_bridge.json — your session, never your password):
       ./.venv/bin/python scripts/avent-login.py --email you@example.com
  2) Serve the camera as RTSP:
       ./bin/avent-webrtc-bridge addon --config philips_avent_bridge.json
  3) Point homebridge-camera-ffmpeg at rtsp://127.0.0.1:38554/<CameraName>
     (see templates/homebridge-camera-config.example.json)
  4) For HomeKit audio you need an ffmpeg with libfdk_aac:
       ./scripts/build-ffmpeg-fdk.sh
EOF
