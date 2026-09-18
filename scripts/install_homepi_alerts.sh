#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  exec sudo -- "$0" "$@"
fi

APP_USER="${SUDO_USER:-stefano}"
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_SRC="${APP_DIR}/systemd/homepi-alert-monitor.service"
UNIT_DST="/etc/systemd/system/homepi-alert-monitor.service"
ENV_FILE="${APP_DIR}/.env.alerts"
ENV_EXAMPLE="${APP_DIR}/.env.alerts.example"
AGI_SRC="${APP_DIR}/scripts/homepi_alert_agi.py"
AGI_DST="/usr/local/lib/homepi-alert-agi.py"
HELPER_SRC="${APP_DIR}/scripts/homepi_systemctl.py"
HELPER_DST="/usr/local/sbin/homepi-systemctl"
SUDOERS_DST="/etc/sudoers.d/homepi-alerts"
SHARED_DIR="/var/spool/asterisk/homepi-alerts"

if ! id "${APP_USER}" >/dev/null 2>&1; then
  echo "User does not exist: ${APP_USER}" >&2
  exit 1
fi

echo "Installing Asterisk and local speech tools..."
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y asterisk espeak-ng sox

if ! getent group asterisk >/dev/null; then
  echo "Asterisk group is missing after package installation." >&2
  exit 1
fi

echo "Granting ${APP_USER} access to the Asterisk spool..."
usermod -aG asterisk "${APP_USER}"

echo "Preparing secure HomePi assistant directories..."
install -d -o "${APP_USER}" -g asterisk -m 2770 /var/spool/asterisk/.homepi-staging
install -d -o "${APP_USER}" -g asterisk -m 2770 "${SHARED_DIR}"
for child in incidents actions ack audio call-staging; do
  install -d -o "${APP_USER}" -g asterisk -m 2770 "${SHARED_DIR}/${child}"
done
install -d -o "${APP_USER}" -g asterisk -m 2770 /var/tmp/homepi-alerts

chgrp asterisk /var/spool/asterisk/outgoing
chmod g+rwx /var/spool/asterisk/outgoing

echo "Installing the interactive Asterisk AGI assistant..."
install -o root -g asterisk -m 0755 "${AGI_SRC}" "${AGI_DST}"

echo "Installing the allowlisted HomePi system-control helper..."
install -o root -g root -m 0755 "${HELPER_SRC}" "${HELPER_DST}"

cat > "${SUDOERS_DST}" <<EOF
# HomePi phone assistant — only exact allowlisted restart actions.
${APP_USER} ALL=(root) NOPASSWD: ${HELPER_DST} restart raspberry-bot.service
${APP_USER} ALL=(root) NOPASSWD: ${HELPER_DST} restart raspberry-dashboard.service
${APP_USER} ALL=(root) NOPASSWD: ${HELPER_DST} restart pihole-FTL.service
EOF
chmod 0440 "${SUDOERS_DST}"
visudo -cf "${SUDOERS_DST}"

if [[ ! -f "${ENV_FILE}" ]]; then
  install -o "${APP_USER}" -g "${APP_USER}" -m 0600 "${ENV_EXAMPLE}" "${ENV_FILE}"
  echo "Created ${ENV_FILE} with alerts disabled."
else
  chown "${APP_USER}:${APP_USER}" "${ENV_FILE}"
  chmod 0600 "${ENV_FILE}"

  # Add new interactive defaults without overwriting existing choices.
  grep -q '^HOMEPI_ALERT_INTERACTIVE=' "${ENV_FILE}" || echo 'HOMEPI_ALERT_INTERACTIVE=true' >> "${ENV_FILE}"
  grep -q '^HOMEPI_ALERT_ESCALATION_SECONDS=' "${ENV_FILE}" || echo 'HOMEPI_ALERT_ESCALATION_SECONDS=300' >> "${ENV_FILE}"
  grep -q '^HOMEPI_ALERT_MAX_ESCALATIONS=' "${ENV_FILE}" || echo 'HOMEPI_ALERT_MAX_ESCALATIONS=2' >> "${ENV_FILE}"
  grep -q '^HOMEPI_ALERT_ACTION_TIMEOUT_SECONDS=' "${ENV_FILE}" || echo 'HOMEPI_ALERT_ACTION_TIMEOUT_SECONDS=30' >> "${ENV_FILE}"
  grep -q '^HOMEPI_ALERT_ACTION_PIN=' "${ENV_FILE}" || echo 'HOMEPI_ALERT_ACTION_PIN=' >> "${ENV_FILE}"
  grep -q '^HOMEPI_ALERT_RESTARTABLE_SERVICES=' "${ENV_FILE}" || echo 'HOMEPI_ALERT_RESTARTABLE_SERVICES=raspberry-bot.service,raspberry-dashboard.service,pihole-FTL.service' >> "${ENV_FILE}"
  grep -q '^HOMEPI_ALERT_SHARED_DIR=' "${ENV_FILE}" || echo 'HOMEPI_ALERT_SHARED_DIR=/var/spool/asterisk/homepi-alerts' >> "${ENV_FILE}"
  grep -q '^HOMEPI_ALERT_AGI_SCRIPT=' "${ENV_FILE}" || echo 'HOMEPI_ALERT_AGI_SCRIPT=/usr/local/lib/homepi-alert-agi.py' >> "${ENV_FILE}"
fi

ACTION_PIN="$(grep '^HOMEPI_ALERT_ACTION_PIN=' "${ENV_FILE}" | tail -n1 | cut -d= -f2- | tr -d '[:space:]')"
if [[ ! "${ACTION_PIN}" =~ ^[0-9]{4,8}$ ]]; then
  ACTION_PIN="$(python3 -c 'import secrets; print(secrets.randbelow(900000) + 100000)')"
  sed -i "s/^HOMEPI_ALERT_ACTION_PIN=.*/HOMEPI_ALERT_ACTION_PIN=${ACTION_PIN}/" "${ENV_FILE}"
  echo "Generated phone action PIN: ${ACTION_PIN}"
  echo "Store this PIN; it is required for restart option 3."
fi
chown "${APP_USER}:${APP_USER}" "${ENV_FILE}"
chmod 0600 "${ENV_FILE}"

if [[ "${APP_USER}" != "stefano" || "${APP_DIR}" != "/home/stefano/services/Raspberry-Bot" ]]; then
  sed \
    -e "s|^User=stefano$|User=${APP_USER}|" \
    -e "s|^Group=stefano$|Group=${APP_USER}|" \
    -e "s|/home/stefano/services/Raspberry-Bot|${APP_DIR}|g" \
    "${UNIT_SRC}" > "${UNIT_DST}"
else
  install -m 0644 "${UNIT_SRC}" "${UNIT_DST}"
fi

systemctl daemon-reload
systemctl enable --now asterisk.service
systemctl enable --now homepi-alert-monitor.service

echo
echo "HomePi phone assistant installed."
echo "Interactive mode and escalation are prepared."
echo
echo "Automatic calls remain disabled until HOMEPI_ALERTS_ENABLED=true is set in:"
echo "  ${ENV_FILE}"
echo
echo "Next setup:"
echo "  cd ${APP_DIR}"
echo "  sudo bash scripts/configure_homepi_alerts.sh"
echo "  .venv/bin/python scripts/homepi_alertctl.py doctor"
echo "  sudo systemctl status homepi-alert-monitor --no-pager"
echo
echo "After SIP is configured, queue an explicit test call with:"
echo "  cd ${APP_DIR} && .venv/bin/python scripts/homepi_alertctl.py test-call"
echo
echo "Note: group membership for ${APP_USER} is refreshed on the next login/session."
