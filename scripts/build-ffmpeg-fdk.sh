#!/usr/bin/env bash
# build-ffmpeg-fdk.sh — build a local ffmpeg with libfdk_aac (+ VideoToolbox and
# libx264 on macOS), needed for HomeKit camera AUDIO. HomeKit requires AAC-ELD,
# which only libfdk_aac provides; the stock Homebrew ffmpeg and the
# ffmpeg-for-homebridge prebuilt (no macOS-arm64 binary) do not include it.
#
# Why build by hand: `brew install homebrew-ffmpeg/ffmpeg/ffmpeg --with-fdk-aac`
# refuses when Xcode.app is older than Homebrew expects ("Your Xcode is too
# outdated"), even with the Command Line Tools present. Compiling directly with
# the CLT clang sidesteps that policy check entirely.
#
# Result: ./ffmpeg-fdk/bin/ffmpeg  (dynamically linked against Homebrew's
# fdk-aac and x264 — if brew removes/upgrades those, re-run this).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PREFIX="$ROOT/ffmpeg-fdk"
FFMPEG_VERSION="${FFMPEG_VERSION:-7.1.1}"

echo "==> Installing build deps (fdk-aac, x264, nasm, pkg-config)"
if command -v brew >/dev/null; then
  brew install fdk-aac x264 nasm pkg-config >/dev/null || true
  BREW_PREFIX="$(brew --prefix)"
else
  echo "Homebrew not found; ensure fdk-aac + x264 dev libs and pkg-config are installed."
  BREW_PREFIX="/usr/local"
fi

echo "==> Fetching ffmpeg $FFMPEG_VERSION source"
SRC="$ROOT/.build/ffmpeg-$FFMPEG_VERSION"
mkdir -p "$ROOT/.build"
if [ ! -d "$SRC" ]; then
  curl -sL "https://ffmpeg.org/releases/ffmpeg-$FFMPEG_VERSION.tar.xz" -o "$ROOT/.build/ffmpeg.tar.xz"
  tar xf "$ROOT/.build/ffmpeg.tar.xz" -C "$ROOT/.build"
fi

echo "==> Configuring (libfdk_aac + videotoolbox + libx264)"
export PKG_CONFIG_PATH="$BREW_PREFIX/opt/fdk-aac/lib/pkgconfig:$BREW_PREFIX/opt/x264/lib/pkgconfig:$BREW_PREFIX/lib/pkgconfig"
cd "$SRC"
./configure --prefix="$PREFIX" --cc=clang \
  --enable-gpl --enable-nonfree --enable-version3 \
  --enable-libfdk-aac --enable-libx264 --enable-videotoolbox --enable-audiotoolbox \
  --disable-doc --disable-htmlpages --disable-manpages --disable-txtpages --disable-ffplay \
  --extra-cflags="-I$BREW_PREFIX/include" --extra-ldflags="-L$BREW_PREFIX/lib"

echo "==> Building"
make -j"$(sysctl -n hw.ncpu 2>/dev/null || nproc)"
make install

echo
"$PREFIX/bin/ffmpeg" -hide_banner -encoders | grep -E "libfdk_aac|h264_videotoolbox" || true
echo "Built: $PREFIX/bin/ffmpeg"
echo "Set this as 'videoProcessor' in your homebridge-camera-ffmpeg config to enable audio."
