#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="${BOT_REPO_PATH:-/home/stefano/services/Raspberry-Bot}"
TARGET_USER="${DISPLAY_SERVICE_USER:-stefano}"
VENV_PY="$REPO_ROOT/.venv/bin/python"
VENV_PIP="$REPO_ROOT/.venv/bin/pip"
DISPLAY2_UNIT="raspberry-display2.service"
MESH_UNIT="raspberry-meshtastic.service"
OVERLAY_LINE="dtoverlay=i2c-gpio,bus=3,i2c_gpio_sda=23,i2c_gpio_scl=24"

if [[ ! -d "$REPO_ROOT" ]]; then
  echo "Repo nicht gefunden: $REPO_ROOT" >&2
  exit 1
fi
if [[ ! -x "$VENV_PY" || ! -x "$VENV_PIP" ]]; then
  echo "Python-vEnv fehlt unter $REPO_ROOT/.venv" >&2
  exit 1
fi

cd "$REPO_ROOT"

echo "[1/7] Systempakete"
sudo apt update
sudo apt install -y i2c-tools python3-dev libjpeg-dev zlib1g-dev

echo "[2/7] Benutzerrechte"
if getent group i2c >/dev/null 2>&1; then
  sudo usermod -aG i2c "$TARGET_USER" || true
fi
if getent group dialout >/dev/null 2>&1; then
  sudo usermod -aG dialout "$TARGET_USER" || true
fi

echo "[3/7] Software-I2C Bus 3 vorbereiten (GPIO23 SDA / GPIO24 SCL)"
BOOT_CONFIG=""
for candidate in /boot/firmware/config.txt /boot/config.txt; do
  if [[ -f "$candidate" ]]; then
    BOOT_CONFIG="$candidate"
    break
  fi
done

REBOOT_NEEDED=0
if [[ -n "$BOOT_CONFIG" ]]; then
  if ! grep -Fqx "$OVERLAY_LINE" "$BOOT_CONFIG"; then
    echo "$OVERLAY_LINE" | sudo tee -a "$BOOT_CONFIG" >/dev/null
    REBOOT_NEEDED=1
    echo "Overlay eingetragen: $BOOT_CONFIG"
  else
    echo "Overlay bereits vorhanden."
  fi
else
  echo "WARNUNG: config.txt nicht gefunden. Software-I2C konnte nicht automatisch eingetragen werden."
fi

echo "[4/7] Python-Abhängigkeiten"
"$VENV_PIP" install -r requirements-display.txt
"$VENV_PIP" install -r requirements-meshtastic.txt

echo "[5/7] Konfigurationsdateien"
if [[ ! -f "$REPO_ROOT/.env.display2" ]]; then
  cp "$REPO_ROOT/.env.display2.example" "$REPO_ROOT/.env.display2"
  echo ".env.display2 angelegt."
fi
if [[ ! -f "$REPO_ROOT/.env.meshtastic" ]]; then
  cp "$REPO_ROOT/.env.meshtastic.example" "$REPO_ROOT/.env.meshtastic"
  echo ".env.meshtastic angelegt."
fi

echo "[6/7] systemd installieren"
sudo install -m 0644 "$REPO_ROOT/systemd/$DISPLAY2_UNIT" "/etc/systemd/system/$DISPLAY2_UNIT"
sudo install -m 0644 "$REPO_ROOT/systemd/$MESH_UNIT" "/etc/systemd/system/$MESH_UNIT"
sudo systemctl daemon-reload
sudo systemctl enable --now "$MESH_UNIT"
sudo systemctl enable --now "$DISPLAY2_UNIT"

echo "[7/7] Checks"
"$VENV_PY" -m display_service.secondary --check || true

echo
echo "I2C-Geräte aktuell:"
ls -1 /dev/i2c-* 2>/dev/null || true

echo
echo "Fertig."
if [[ "$REBOOT_NEEDED" -eq 1 ]]; then
  echo "WICHTIG: Einmal neu starten, damit /dev/i2c-3 erscheint:"
  echo "  sudo reboot"
else
  echo "Falls /dev/i2c-3 bereits existiert, kannst du Display 2 sofort testen:"
  echo "  sudo i2cdetect -y 3"
fi

echo
echo "Display 2 Status:  $REPO_ROOT/data/display2_status.json"
echo "Display 2 Preview: $REPO_ROOT/data/display2_preview.png"
echo "Mesh Status:       $REPO_ROOT/data/meshtastic_state.json"
echo
echo "Logs:"
echo "  journalctl -u raspberry-display2 -n 80 --no-pager"
echo "  journalctl -u raspberry-meshtastic -n 80 --no-pager"
