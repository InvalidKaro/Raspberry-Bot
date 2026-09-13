#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -d .venv ]]; then
  echo "Missing .venv. Install the Raspberry-Bot environment first." >&2
  exit 1
fi

if [[ ! -f .env.homepi ]]; then
  cp .env.homepi.example .env.homepi
  chmod 600 .env.homepi
  echo "Created .env.homepi from template."
fi

DEFAULT_WATCH_SERVICES="raspberry-bot.service,raspberry-dashboard.service,homepi-flight-radar.service,raspberry-intelligence.service,raspberry-display.service,raspberry-display2.service,raspberry-meshtastic.service,pihole-FTL.service,tailscaled.service,ssh.service"
if ! grep -Eq '^[[:space:]]*HOMEPI_WATCH_SERVICES=' .env.homepi; then
  printf '\n# HomePi services monitored by Blackbox / Server Score.\nHOMEPI_WATCH_SERVICES=%s\n' "$DEFAULT_WATCH_SERVICES" >> .env.homepi
  chmod 600 .env.homepi || true
  echo "Added expanded HomePi service watchlist."
fi

mkdir -p data
.venv/bin/python -m homepi_intelligence --check
.venv/bin/python scripts/prune_homepi_blackbox.py --dry-run

sudo install -m 0644 systemd/raspberry-intelligence.service /etc/systemd/system/raspberry-intelligence.service
sudo install -m 0644 systemd/raspberry-blackbox-prune.service /etc/systemd/system/raspberry-blackbox-prune.service
sudo install -m 0644 systemd/raspberry-blackbox-prune.timer /etc/systemd/system/raspberry-blackbox-prune.timer
sudo systemctl daemon-reload
sudo systemctl enable --now raspberry-intelligence.service
sudo systemctl enable --now raspberry-blackbox-prune.timer
sudo systemctl --no-pager --full status raspberry-intelligence.service || true
sudo systemctl --no-pager --full status raspberry-blackbox-prune.timer || true

echo
echo "HomePi intelligence installed."
echo "Configure HOMEPI_WARNING_ARS in $ROOT_DIR/.env.homepi to enable regional warnings."
echo "Blackbox retention runs daily and honors HOMEPI_BLACKBOX_RETENTION_DAYS."
