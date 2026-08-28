#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

git pull --ff-only
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m compileall -q .

if systemctl list-unit-files raspberry-bot.service >/dev/null 2>&1; then
  sudo systemctl restart raspberry-bot
  sudo systemctl --no-pager --full status raspberry-bot || true
else
  echo "raspberry-bot.service is not installed; code updated without restarting a service."
fi
