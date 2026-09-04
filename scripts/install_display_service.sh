#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="${BOT_REPO_PATH:-/home/stefano/services/Raspberry-Bot}"
SERVICE_NAME="raspberry-display"
SERVICE_FILE="$REPO_ROOT/systemd/${SERVICE_NAME}.service"
VENV_PY="$REPO_ROOT/.venv/bin/python"
VENV_PIP="$REPO_ROOT/.venv/bin/pip"
TARGET_USER="${DISPLAY_SERVICE_USER:-stefano}"

if [[ ! -d "$REPO_ROOT" ]]; then
  echo "Repo nicht gefunden: $REPO_ROOT" >&2
  exit 1
fi
if [[ ! -x "$VENV_PY" || ! -x "$VENV_PIP" ]]; then
  echo "Python-vEnv fehlt unter $REPO_ROOT/.venv" >&2
  exit 1
fi
if [[ ! -f "$SERVICE_FILE" ]]; then
  echo "Service-Datei fehlt: $SERVICE_FILE" >&2
  exit 1
fi

cd "$REPO_ROOT"

echo "[1/6] Systempakete installieren"
sudo apt update
sudo apt install -y i2c-tools python3-dev libjpeg-dev zlib1g-dev

echo "[2/6] I2C vorbereiten"
if command -v raspi-config >/dev/null 2>&1; then
  sudo raspi-config nonint do_i2c 0 || true
fi
if getent group i2c >/dev/null 2>&1; then
  sudo usermod -aG i2c "$TARGET_USER" || true
else
  echo "Hinweis: i2c-Gruppe existiert noch nicht. Der Service läuft trotzdem im Standby."
fi

echo "[3/6] Display-Abhängigkeiten installieren"
"$VENV_PIP" install -r requirements-display.txt

echo "[4/6] Deployment-Check"
DISPLAY_ALLOW_MISSING_HARDWARE=1 "$VENV_PY" -m display_service.main --check

echo "[5/6] systemd installieren"
sudo install -m 0644 "$SERVICE_FILE" "/etc/systemd/system/${SERVICE_NAME}.service"
sudo systemctl daemon-reload
sudo systemctl enable --now "$SERVICE_NAME"

echo "[6/6] Status"
sudo systemctl --no-pager --full status "$SERVICE_NAME" || true

echo
echo "Fertig. Ohne OLED ist 'standby' normal."
echo "Statusdatei: $REPO_ROOT/data/display_status.json"
echo "Headless-Preview: $REPO_ROOT/data/display_preview.png"
echo "Wenn das OLED später angeschlossen wird, erkennt der laufende Service es automatisch."
echo "Diagnose: $VENV_PY -m display_service.main --check"
