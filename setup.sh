#!/bin/sh
# Set up and start Magpie with Docker in one step:
#
#   ./setup.sh
#
# It asks for your Claude API key, a password and your time zone, writes them
# to .env, and starts Magpie at http://localhost:8000. Run it again any time to
# change those answers; your notes are kept.
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
echo
echo "Magpie is running at http://localhost:8000"
echo "Check your setup any time with: docker compose exec magpie magpie doctor"
