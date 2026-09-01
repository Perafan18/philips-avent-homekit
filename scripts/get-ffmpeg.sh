#!/usr/bin/env bash
# get-ffmpeg.sh — download the official static ffmpeg from ffmpeg-for-homebridge
# (has libfdk_aac for HomeKit AAC-ELD audio, plus hardware accel). This is the
# easy path; build-ffmpeg-fdk.sh is only a fallback if no prebuilt fits you.
# Result: ./ffmpeg-static/ffmpeg  — point 'videoProcessor' at it.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="${FFMPEG_FOR_HOMEBRIDGE_VERSION:-v2.2.2}"

os="$(uname -s)"; arch="$(uname -m)"
case "$os/$arch" in
  Darwin/arm64)  asset="ffmpeg-darwin-arm64.tar.gz" ;;
  Darwin/x86_64) asset="ffmpeg-darwin-x86_64.tar.gz" ;;
  Linux/aarch64|Linux/arm64) asset="ffmpeg-alpine-aarch64.tar.gz" ;;
  Linux/x86_64)  asset="ffmpeg-alpine-x86_64.tar.gz" ;;
  Linux/armv7l)  asset="ffmpeg-alpine-arm32v7.tar.gz" ;;
  *) echo "No prebuilt for $os/$arch — use scripts/build-ffmpeg-fdk.sh instead."; exit 1 ;;
esac

url="https://github.com/homebridge/ffmpeg-for-homebridge/releases/download/$VERSION/$asset"
dest="$ROOT/ffmpeg-static"; mkdir -p "$dest"
echo "==> Downloading $asset ($VERSION)"
curl -sL "$url" -o "$dest/ff.tar.gz"
tar xf "$dest/ff.tar.gz" -C "$dest"
bin="$(find "$dest" -name ffmpeg -type f | head -1)"
mv "$bin" "$dest/ffmpeg"; chmod +x "$dest/ffmpeg"
rm -rf "$dest/usr" "$dest/ff.tar.gz"

echo "==> Pin this SHA-256 (verify it doesn't change unexpectedly):"
shasum -a 256 "$dest/ffmpeg" 2>/dev/null || sha256sum "$dest/ffmpeg"
"$dest/ffmpeg" -hide_banner -encoders 2>/dev/null | grep -E "libfdk_aac|h264_videotoolbox" || true
echo "Set videoProcessor to: $dest/ffmpeg"
