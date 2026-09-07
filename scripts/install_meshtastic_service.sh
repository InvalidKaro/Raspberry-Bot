#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="${BOT_REPO_PATH:-/home/stefano/services/Raspberry-Bot}"
TARGET_USER="${MESHTASTIC_SERVICE_USER:-stefano}"
VENV_PY="$REPO_ROOT/.venv/bin/python"
VENV_PIP="$REPO_ROOT/.venv/bin/pip"
UNIT="raspberry-meshtastic.service"

if [[ ! -d "$REPO_ROOT" ]]; then
  echo "Repo nicht gefunden: $REPO_ROOT" >&2
  exit 1
fi
if [[ ! -x "$VENV_PY" || ! -x "$VENV_PIP" ]]; then
  echo "Python-vEnv fehlt unter $REPO_ROOT/.venv" >&2
  exit 1
fi

cd "$REPO_ROOT"

echo "[1/5] Benutzerrechte"
if getent group dialout >/dev/null 2>&1; then
  sudo usermod -aG dialout "$TARGET_USER" || true
fi

echo "[2/5] Python-Abhängigkeiten"
"$VENV_PIP" install -r requirements-meshtastic.txt

echo "[3/5] Konfiguration"
if [[ ! -f "$REPO_ROOT/.env.meshtastic" ]]; then
  cp "$REPO_ROOT/.env.meshtastic.example" "$REPO_ROOT/.env.meshtastic"
  echo ".env.meshtastic angelegt."
fi

echo "[4/5] systemd installieren"
sudo install -m 0644 "$REPO_ROOT/systemd/$UNIT" "/etc/systemd/system/$UNIT"
sudo systemctl daemon-reload
sudo systemctl enable --now "$UNIT"

echo "[5/5] Status"
echo "Serielle Geräte:"
ls -l /dev/ttyUSB* /dev/ttyACM* 2>/dev/null || true

echo
sudo systemctl --no-pager --full status "$UNIT" || true

echo
echo "Fertig."
echo "State: $REPO_ROOT/data/meshtastic_state.json"
echo "Dashboard: /meshtastic"
echo "Logs: journalctl -u $UNIT -n 100 --no-pager"
echo
echo "Falls mehrere serielle Geräte vorhanden sind, MESHTASTIC_DEVICE in"
echo "$REPO_ROOT/.env.meshtastic setzen, z. B. /dev/ttyUSB0."
echo "Nach erstmaligem Hinzufügen zur Gruppe dialout kann einmaliges Ab-/Anmelden oder ein Reboot nötig sein."
