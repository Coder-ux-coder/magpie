#!/bin/sh
# Open Magpie in its own window (Mac and Linux). The setup (sh setup.sh) makes a
# Magpie app that runs this, so you don't need to type an address:
#
#   sh magpie.sh
#
# It starts Docker and Magpie if they aren't running, waits until Magpie
# answers, then opens it in an app window (Chrome, Edge or Chromium) or, if none
# of those is installed, in your usual browser.
set -u
cd "$(dirname "$0")"
# Apps started from the Dock or a login item get a very short PATH.
PATH="$PATH:/usr/local/bin:/opt/homebrew/bin:$HOME/.docker/bin:/Applications/Docker.app/Contents/Resources/bin"
# MAGPIE_HOST_PORT in .env moves Magpie off port 8000.
port=$(sed -n "s/^MAGPIE_HOST_PORT=[\"']\{0,1\}\([0-9][0-9]*\).*/\1/p" .env 2>/dev/null | tail -n 1)
url="http://localhost:${port:-8000}"

say() {
  echo "$1" >&2
}

up() {
  curl -fsS -o /dev/null --max-time 3 "$url/healthz" 2>/dev/null
}

if ! up; then
  if ! command -v docker >/dev/null 2>&1; then
    say "Magpie runs in Docker, which isn't installed. Install Docker, then run sh setup.sh."
    exit 1
  fi
  if ! docker info >/dev/null 2>&1; then
    if [ "$(uname -s)" = Darwin ] && open -ga Docker 2>/dev/null; then
      say "Starting Docker Desktop..."
    fi
    i=0
    until docker info >/dev/null 2>&1; do
      i=$((i + 1))
      if [ "$i" -ge 120 ]; then
        say "Docker isn't running. Start Docker Desktop (or the Docker service), then open Magpie again."
        exit 1
      fi
      sleep 1
    done
  fi
  # Start Magpie if it was stopped (or create it again if it was removed); this
  # never changes your notes or settings.
  docker compose start >/dev/null 2>&1 || docker compose up -d >/dev/null 2>&1 || true
  printf "Starting Magpie"
  i=0
  until up; do
    i=$((i + 1))
    if [ "$i" -ge 120 ]; then
      echo
      say "Magpie didn't start. To see why, run: docker compose logs magpie"
      exit 1
    fi
    printf "."
    sleep 1
  done
  echo
fi

# An app window: no address bar or tabs, like any other program.
if [ "$(uname -s)" = Darwin ]; then
  for app in "Google Chrome" "Microsoft Edge" "Chromium" "Brave Browser"; do
    if [ -d "/Applications/$app.app" ] || [ -d "$HOME/Applications/$app.app" ]; then
      open -na "$app" --args --app="$url" && exit 0
    fi
  done
  open "$url"
  exit $?
fi
if [ -z "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then
  say "Magpie is running. Open $url in your browser."
  exit 0
fi
for browser in google-chrome google-chrome-stable chromium chromium-browser microsoft-edge microsoft-edge-stable brave-browser; do
  if command -v "$browser" >/dev/null 2>&1; then
    nohup "$browser" --app="$url" >/dev/null 2>&1 &
    exit 0
  fi
done
if command -v xdg-open >/dev/null 2>&1; then
  xdg-open "$url" >/dev/null 2>&1 &
  exit 0
fi
say "Magpie is running. Open $url in your browser."
