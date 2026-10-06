#!/usr/bin/env bash
# One-time server setup on Ubuntu 22.04/24.04. Run from the cloned repo:
#   sudo ./deploy/install.sh
set -euo pipefail

cd "$(dirname "$0")/.."

if ! command -v docker >/dev/null; then
  echo "==> Installing Docker"
  apt-get update -y
  apt-get install -y ca-certificates curl
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -y
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
fi

if [ ! -f .env ]; then
  echo "==> Creating .env with a random dashboard password"
  cp .env.example .env
  pass=$(openssl rand -base64 18 | tr -d '/+=')
  sed -i "s/^DASHBOARD_PASSWORD=.*/DASHBOARD_PASSWORD=${pass}/" .env
  echo "    Dashboard login: admin / ${pass}  (stored in .env)"
fi

# The container runs as uid 1000.
mkdir -p data
chown -R 1000:1000 data

if [ -n "${SUDO_USER:-}" ]; then
  usermod -aG docker "$SUDO_USER"
  chown "$SUDO_USER" .env
fi
chmod 600 .env

echo "==> Done. Re-login (for the docker group), then run ./deploy/update.sh"
