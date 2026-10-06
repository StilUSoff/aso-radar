#!/usr/bin/env bash
# Pull the latest code and (re)start the service. Uses HTTPS via Caddy when DOMAIN is set.
set -euo pipefail

cd "$(dirname "$0")/.."
git pull --ff-only

profile=()
if grep -qE '^DOMAIN=.+' .env; then
  profile=(--profile https)
fi

docker compose "${profile[@]}" up -d --build --remove-orphans
docker image prune -f >/dev/null
docker compose "${profile[@]}" ps
