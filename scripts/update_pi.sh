#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

git pull --ff-only
source .venv/bin/activate
python -m pip install -r requirements.txt

if [[ -f requirements-display.txt ]]; then
  python -m pip install -r requirements-display.txt
fi
if [[ -f requirements-meshtastic.txt ]]; then
  python -m pip install -r requirements-meshtastic.txt
fi

# Compile only project code. The venv contains third-party packages with legacy
# contrib modules that are intentionally not Python 3 compatible and must not
# make the HomePi update fail.
python -m compileall -q -x '(^|/)(\.venv|\.git)(/|$)' .

SERVICES=(
  raspberry-bot.service
  raspberry-dashboard.service
  raspberry-display.service
  raspberry-meshtastic.service
  raspberry-display2.service
)

for service in "${SERVICES[@]}"; do
  if systemctl list-unit-files "$service" --no-legend 2>/dev/null | grep -q "^${service}"; then
    echo "Restarting $service"
    sudo systemctl restart "$service"
    sudo systemctl --no-pager --full status "$service" || true
  fi
done
