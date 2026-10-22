#!/bin/sh
# Set up and start Magpie with Docker in one step (Mac and Linux):
#
#   sh setup.sh
#
# It asks a few questions (a password and your time zone), writes the
# answers to .env, starts Magpie and opens http://localhost:8000. Magpie then
# keeps itself up to date. Run this again any time to change your answers;
# your notes are kept. On Windows, use setup.cmd.
set -eu
cd "$(dirname "$0")"

if ! command -v docker >/dev/null 2>&1; then
  echo "Magpie runs in Docker. Install it first: https://docs.docker.com/get-docker/" >&2
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  echo "This needs Docker Compose v2 (the \"docker compose\" command). Update Docker and try again." >&2
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  echo "Docker is installed but not running. Start Docker Desktop, wait until it says it's running, then run this again." >&2
  exit 1
fi

# The container can't see this computer's time zone, so pass it in as a suggestion.
zone="${TZ:-}"
if [ -z "$zone" ] && command -v timedatectl >/dev/null 2>&1; then
  zone="$(timedatectl show -p Timezone --value 2>/dev/null || true)"
fi
if [ -z "$zone" ] && [ -L /etc/localtime ]; then
  zone="$(readlink /etc/localtime | sed -n 's|.*zoneinfo/||p')"
fi

# The updater restarts Magpie through Docker. Rootless Docker on Linux keeps its
# socket somewhere else, so remember where for later restarts.
if [ "$(uname -s)" = Linux ]; then
  host="${DOCKER_HOST:-$(docker context inspect --format '{{.Endpoints.docker.Host}}' 2>/dev/null || true)}"
  socket="${host#unix://}"
  if [ "$socket" != "$host" ] && [ "$socket" != /var/run/docker.sock ] && [ -S "$socket" ]; then
    export DOCKER_SOCKET="$socket"
    if ! grep -q '^DOCKER_SOCKET=' .env 2>/dev/null; then
      printf 'DOCKER_SOCKET=%s\n' "$socket" >> .env
    fi
  fi
fi

echo "Downloading Magpie (the first time takes a minute or two)..."
if ! docker compose pull --quiet magpie; then
  echo "Couldn't download Magpie, so building it on this computer instead (a few minutes)..."
  docker build --quiet -t "$(docker compose config --images magpie | head -n 1)" .
fi
docker compose pull --quiet updates >/dev/null 2>&1 || true

docker compose run --rm --no-deps \
  --user "$(id -u):$(id -g)" \
  -e HOME=/tmp \
  -e TZ="$zone" \
  -v "$PWD:/setup" -w /setup \
  magpie magpie setup --env-file /setup/.env "$@"

docker compose up -d magpie
if ! docker compose up -d updates >/dev/null 2>&1; then
  echo "Automatic updates couldn't start, but Magpie works. To update by hand, run this setup again."
fi
# The image older versions built on this computer isn't needed any more.
docker image rm magpie:local >/dev/null 2>&1 || true

# MAGPIE_HOST_PORT in .env moves Magpie off port 8000.
port=$(sed -n "s/^MAGPIE_HOST_PORT=[\"']\{0,1\}\([0-9][0-9]*\).*/\1/p" .env 2>/dev/null | tail -n 1)
url="http://localhost:${port:-8000}"
if command -v curl >/dev/null 2>&1; then
  printf "Starting Magpie"
  for _ in $(seq 1 60); do
    curl -fsS "$url/healthz" >/dev/null 2>&1 && break
    printf "."
    sleep 1
  done
  echo
fi

# A Magpie app, so you never need to type the address. Running this setup again
# replaces it rather than adding another.
yes_to_all=no
for arg in "$@"; do
  if [ "$arg" = --yes ]; then yes_to_all=yes; fi
done
ask() {
  # Enter means yes; so does running without a terminal or with --yes.
  if [ "$yes_to_all" = no ] && [ -t 0 ]; then
    printf '%s [Y/n] ' "$1"
    read -r answer || answer=
    case "$answer" in [Nn]*) return 1 ;; esac
  fi
  return 0
}
here="$PWD"
icon="$here/src/magpie/web/static/icons/icon-512.png"
case "$(uname -s)" in
  Darwin)
    app="$HOME/Applications/Magpie.app"
    script="$(printf '%s' "$here/magpie.sh" | sed 's/\\/\\\\/g; s/"/\\"/g')"
    mkdir -p "$HOME/Applications"
    rm -rf "$app"
    if osacompile -o "$app" \
      -e 'display notification "Starting Magpie..." with title "Magpie"' \
      -e 'try' \
      -e "do shell script \"sh \" & quoted form of \"$script\"" \
      -e 'on error errorText' \
      -e 'display dialog errorText buttons {"OK"} default button 1 with title "Magpie"' \
      -e 'end try' >/dev/null 2>&1; then
      # Give the app the Magpie icon (a plain one is fine if this doesn't work).
      (
        set +e
        work=$(mktemp -d) || exit 0
        mkdir "$work/magpie.iconset"
        for size in 16 32 128 256 512; do
          sips -z "$size" "$size" "$icon" --out "$work/magpie.iconset/icon_${size}x${size}.png"
          if [ "$size" -le 256 ]; then
            sips -z $((size * 2)) $((size * 2)) "$icon" \
              --out "$work/magpie.iconset/icon_${size}x${size}@2x.png"
          fi
        done
        iconutil -c icns "$work/magpie.iconset" -o "$app/Contents/Resources/applet.icns" &&
          touch "$app"
        rm -rf "$work"
      ) >/dev/null 2>&1 || true
      echo "Magpie is in your Applications folder and Launchpad: open it from there."
      if ask "Open Magpie when you log in?"; then
        osascript -e "tell application \"System Events\" to if not (exists login item \"Magpie\") then make login item at end with properties {path:\"$HOME/Applications/Magpie.app\", hidden:false}" >/dev/null 2>&1 ||
          echo "Couldn't add it. You can add Magpie in System Settings > General > Login Items."
      else
        osascript -e 'tell application "System Events" to if (exists login item "Magpie") then delete login item "Magpie"' >/dev/null 2>&1 || true
      fi
    else
      echo "Couldn't make the Magpie app. You can open Magpie with: sh magpie.sh"
    fi
    ;;
  Linux)
    if [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then
      entry="$HOME/.local/share/applications/magpie.desktop"
      if mkdir -p "$(dirname "$entry")" && cat > "$entry" <<EOF
[Desktop Entry]
Type=Application
Name=Magpie
Comment=Voice notes that remind you
Exec=sh "$here/magpie.sh"
Icon=$icon
Terminal=false
Categories=Office;Utility;
EOF
      then
        echo "Magpie is in your applications menu: open it from there."
        if ask "Open Magpie when you log in?"; then
          mkdir -p "$HOME/.config/autostart" && cp "$entry" "$HOME/.config/autostart/magpie.desktop" ||
            echo "Couldn't add it. Copy $entry to ~/.config/autostart to do it yourself."
        else
          rm -f "$HOME/.config/autostart/magpie.desktop"
        fi
      else
        echo "Couldn't add Magpie to your applications menu. You can open it with: sh magpie.sh"
      fi
    fi
    ;;
esac

echo "Magpie is running. It updates itself and starts again by itself whenever Docker starts."
echo "To stop it: docker compose stop"
echo "To check your setup: docker compose exec magpie magpie doctor"
sh ./magpie.sh || true
