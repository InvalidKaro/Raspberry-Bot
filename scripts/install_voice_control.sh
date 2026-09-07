#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="${BOT_REPO_PATH:-/home/stefano/services/Raspberry-Bot}"
DASH_ENV="$REPO_ROOT/.env.dashboard"
HELPER_SRC="$REPO_ROOT/scripts/homepi_systemctl.py"
HELPER_DST="/usr/local/sbin/homepi-systemctl"
SUDOERS_SRC="$REPO_ROOT/sudoers/raspberry-dashboard"
SUDOERS_DST="/etc/sudoers.d/raspberry-dashboard"

if [[ ! -d "$REPO_ROOT" ]]; then
  echo "Repo nicht gefunden: $REPO_ROOT" >&2
  exit 1
fi
if [[ ! -f "$DASH_ENV" ]]; then
  echo ".env.dashboard fehlt: $DASH_ENV" >&2
  exit 1
fi

cd "$REPO_ROOT"

echo "[1/5] Root-owned systemctl helper installieren"
sudo install -o root -g root -m 0755 "$HELPER_SRC" "$HELPER_DST"

echo "[2/5] sudo-Regeln aktualisieren"
sudo install -o root -g root -m 0440 "$SUDOERS_SRC" "$SUDOERS_DST"
sudo visudo -cf "$SUDOERS_DST"

echo "[3/5] Voice API Token vorbereiten"
if ! grep -Eq '^VOICE_API_TOKEN=.{24,}$' "$DASH_ENV"; then
  TOKEN="$(python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(32))
PY
)"
  if grep -q '^VOICE_API_TOKEN=' "$DASH_ENV"; then
    python3 - "$DASH_ENV" "$TOKEN" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
token = sys.argv[2]
lines = path.read_text(encoding="utf-8").splitlines()
out = []
replaced = False
for line in lines:
    if line.startswith("VOICE_API_TOKEN="):
        out.append(f"VOICE_API_TOKEN={token}")
        replaced = True
    else:
        out.append(line)
if not replaced:
    out.append(f"VOICE_API_TOKEN={token}")
path.write_text("\n".join(out) + "\n", encoding="utf-8")
PY
  else
    printf '\nVOICE_API_TOKEN=%s\n' "$TOKEN" >> "$DASH_ENV"
  fi
  chmod 600 "$DASH_ENV" || true
  echo "Neuer Voice API Token wurde erzeugt."
else
  echo "VOICE_API_TOKEN ist bereits vorhanden."
fi

echo "[4/5] Helper testen"
sudo -n "$HELPER_DST" status raspberry-bot >/dev/null || true

echo "[5/5] Dashboard neu starten"
sudo systemctl restart raspberry-dashboard

TOKEN_VALUE="$(grep '^VOICE_API_TOKEN=' "$DASH_ENV" | tail -n1 | cut -d= -f2-)"

cat <<EOF

Voice Control ist installiert.
Endpoint: http://homepi.local:8080/api/voice-command
Token für den iPhone-Kurzbefehl:
$TOKEN_VALUE

Test vom Pi:
TOKEN='$TOKEN_VALUE'
curl -sS -X POST http://127.0.0.1:8080/api/voice-command \\
  -H "Authorization: Bearer \$TOKEN" \\
  -H 'Content-Type: application/json' \\
  -d '{"text":"HomePi Status"}'
EOF
