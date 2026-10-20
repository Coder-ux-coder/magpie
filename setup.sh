#!/bin/sh
# Set up and start Magpie with Docker in one step (Mac and Linux):
#
#   sh setup.sh
#
# It asks for your Claude API key, a password and your time zone, writes them
# to .env, starts Magpie and opens http://localhost:8000. Run it again any time
# to change those answers; your notes are kept. On Windows, use setup.cmd.
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

echo "Building Magpie (the first time takes a minute or two)..."
docker compose build --quiet magpie

docker compose run --rm --no-deps \
  --user "$(id -u):$(id -g)" \
  -e HOME=/tmp \
  -e TZ="$zone" \
  -v "$PWD:/setup" -w /setup \
  magpie magpie setup --env-file /setup/.env "$@"

docker compose up -d

url="http://localhost:8000"
if command -v curl >/dev/null 2>&1; then
  printf "Starting Magpie"
  for _ in $(seq 1 60); do
    curl -fsS "$url/healthz" >/dev/null 2>&1 && break
    printf "."
    sleep 1
  done
  echo
fi
echo "Magpie is running at $url"
echo "To stop it: docker compose stop. It starts again by itself whenever Docker starts."
echo "To check your setup: docker compose exec magpie magpie doctor"
if command -v open >/dev/null 2>&1; then
  open "$url" 2>/dev/null || true
elif command -v xdg-open >/dev/null 2>&1 && [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then
  xdg-open "$url" >/dev/null 2>&1 || true
fi
