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

echo "Preparing secure HomePi alert directories..."
install -d -o "${APP_USER}" -g asterisk -m 2770 /var/spool/asterisk/.homepi-staging
install -d -o "${APP_USER}" -g "${APP_USER}" -m 0755 /var/tmp/homepi-alerts

chgrp asterisk /var/spool/asterisk/outgoing
chmod g+rwx /var/spool/asterisk/outgoing

if [[ ! -f "${ENV_FILE}" ]]; then
  install -o "${APP_USER}" -g "${APP_USER}" -m 0600 "${ENV_EXAMPLE}" "${ENV_FILE}"
  echo "Created ${ENV_FILE} with alerts disabled."
else
  chown "${APP_USER}:${APP_USER}" "${ENV_FILE}"
  chmod 0600 "${ENV_FILE}"
fi

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
echo "Installed HomePi phone alerts."
echo "Alerts are disabled until HOMEPI_ALERTS_ENABLED=true is set in:"
echo "  ${ENV_FILE}"
echo
echo "Before enabling calls, configure the SIP/PJSIP trunk in Asterisk and verify:"
echo "  asterisk -rx 'pjsip show registrations'"
echo "  systemctl status homepi-alert-monitor --no-pager"
