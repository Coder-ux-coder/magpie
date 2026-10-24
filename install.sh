#!/bin/sh
# Installs (or updates) Magpie on a Mac and opens it. Paste this into Terminal:
#
#   curl -fsSL https://raw.githubusercontent.com/Coder-ux-coder/magpie/main/install.sh | sh
#
# It downloads the latest release, checks it against the release's SHA-256, puts
# Magpie.app in Applications, and waits until Magpie answers. The last line it prints is
# Magpie's address on this Mac, such as http://127.0.0.1:8000. Nothing to answer, and no
# administrator password.
set -eu

repo="Coder-ux-coder/magpie"
release="https://github.com/$repo/releases/latest/download"
home="$HOME/Library/Application Support/Magpie"

fail() {
  echo "$*" >&2
  exit 1
}

[ "$(uname -s)" = "Darwin" ] ||
  fail "This installs the Magpie app on a Mac. For Windows, see https://github.com/$repo#install"
[ "$(uname -m)" = "arm64" ] ||
  fail "The Magpie app is for Macs with Apple silicon (M1 or newer). On this Mac, run it from Python: https://github.com/$repo#from-python"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

if [ -n "${MAGPIE_INSTALL_FROM:-}" ]; then
  # A build to try out (the App workflow's check), instead of the latest release.
  cp "$MAGPIE_INSTALL_FROM" "$work/Magpie-mac.zip"
  version="(this build)"
else
  echo "Finding the latest Magpie..."
  curl -fsSL "$release/latest.json" -o "$work/latest.json"
  value() { plutil -extract "$1" raw -o - "$work/latest.json" 2>/dev/null; }
  version="$(value version)" || fail "The latest release can't be read. Try again later."
  url="$(value mac.url)" || fail "The latest release has no Mac app yet. Try again later."
  sha="$(value mac.sha256)"
  size="$(value mac.size)"
  case "$url" in
    "https://github.com/$repo/releases/download/"*) ;;
    *) fail "The latest release points somewhere unexpected, so it wasn't installed." ;;
  esac
  echo "Downloading Magpie $version ($((size / 1048576)) MB)..."
  curl -fL --progress-bar "$url" -o "$work/Magpie-mac.zip"
  got="$(shasum -a 256 "$work/Magpie-mac.zip" | cut -d ' ' -f 1)"
  if [ "$(stat -f %z "$work/Magpie-mac.zip")" != "$size" ] || [ "$got" != "$sha" ]; then
    fail "The download doesn't match the release, so it wasn't installed. Run this again."
  fi
fi

echo "Installing..."
ditto -x -k "$work/Magpie-mac.zip" "$work/new"
[ -x "$work/new/Magpie.app/Contents/MacOS/Magpie" ] ||
  fail "The download doesn't contain Magpie.app, so nothing was changed."
apps="/Applications"
if [ ! -w "$apps" ]; then
  apps="$HOME/Applications"
  mkdir -p "$apps"
fi
app="$apps/Magpie.app"
# A Magpie that's running quits first, so the new one can take its place.
if pgrep -x Magpie >/dev/null 2>&1; then
  pkill -x Magpie || true
  sleep 2
fi
rm -f "$home/running.json"
rm -rf "$app"
mv "$work/new/Magpie.app" "$app"
xattr -dr com.apple.quarantine "$app" 2>/dev/null || true
echo "Magpie is in $app."

echo "Starting Magpie..."
open "$app"
i=0
while [ "$i" -lt 120 ]; do # the first start can bring notes over from Docker
  if [ -f "$home/running.json" ]; then
    port="$(plutil -extract port raw -o - "$home/running.json" 2>/dev/null || true)"
    if [ -n "$port" ] && curl -fsS "http://127.0.0.1:$port/healthz" >/dev/null 2>&1; then
      echo "Magpie $version is installed and running. Say \"notes on\" in any app."
      echo "http://127.0.0.1:$port"
      exit 0
    fi
  fi
  sleep 2
  i=$((i + 1))
done
fail "Magpie is installed but isn't answering yet. Open it from $apps; its log is in $home/logs."
