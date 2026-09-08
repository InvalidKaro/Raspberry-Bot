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

# Migrate the old Display-2 default page duration. Preserve deliberate custom
# values: only the previous shipped default of 5 seconds is changed to 8.
if [[ -f .env.display2 ]] && grep -qx 'DISPLAY2_PAGE_SECONDS=5' .env.display2; then
  sed -i 's/^DISPLAY2_PAGE_SECONDS=5$/DISPLAY2_PAGE_SECONDS=8/' .env.display2
  echo "Display 2 page duration migrated: 5s -> 8s"
fi

SERVICES=(
  raspberry-bot.service
  raspberry-dashboard.service
  raspberry-display.service
  raspberry-meshtastic.service
  raspberry-display2.service
  raspberry-intelligence.service
)

# Keep already-installed systemd units in sync with the repository. A unit that
# has never been installed is intentionally left alone.
unit_changed=0
for service in "${SERVICES[@]}"; do
  if systemctl list-unit-files "$service" --no-legend 2>/dev/null | grep -q "^${service}"; then
    if [[ -f "systemd/$service" ]]; then
      sudo install -m 0644 "systemd/$service" "/etc/systemd/system/$service"
      unit_changed=1
    fi
  fi
done

# Voice Control uses a root-owned validated helper. Refresh it automatically if
# Voice Control was installed before, so new safe actions/Display-2 events do
# not require rerunning the installer after every git pull.
if [[ -x /usr/local/sbin/homepi-systemctl && -f scripts/homepi_systemctl.py ]]; then
  sudo install -o root -g root -m 0755 scripts/homepi_systemctl.py /usr/local/sbin/homepi-systemctl
  if [[ -f sudoers/raspberry-dashboard ]]; then
    sudo install -o root -g root -m 0440 sudoers/raspberry-dashboard /etc/sudoers.d/raspberry-dashboard
    sudo visudo -cf /etc/sudoers.d/raspberry-dashboard
  fi
fi

if [[ "$unit_changed" -eq 1 ]]; then
  sudo systemctl daemon-reload
fi

for service in "${SERVICES[@]}"; do
  if systemctl list-unit-files "$service" --no-legend 2>/dev/null | grep -q "^${service}"; then
    echo "Restarting $service"
    sudo systemctl restart "$service"
    sudo systemctl --no-pager --full status "$service" || true
  fi
done
