# HomePi Dashboard — Phase 3

Phase 3 upgrades the existing Phase 2 dashboard. It is a drop-in replacement for the dashboard files; your existing `.env.dashboard` is kept.

## New in Phase 3

- richer overview with Tailscale and bot database activity
- System page: services, Tailscale peers, top RAM processes, typed-confirm reboot/shutdown
- Code & Files: create files/directories, rename, delete, project-wide text search, multi-file browser tabs, Python/JSON validation
- Git: stage, unstage, discard tracked changes, branches, create/switch branch, history, pull/push, commit, diff
- Deploy: install `requirements.txt`, compile check, restart/health check, rollback
- Bot Data: ticket/moderation/command stats, recent tickets/cases, static extension + slash-command inventory
- SQLite backup center: create/download/restore/delete backups
- Dashboard audit trail
- login attempt throttling

## Upgrade on the Pi

After copying this package into the existing `Raspberry-Bot` repository and pushing it to GitHub:

```bash
cd ~/services/Raspberry-Bot
git pull
source .venv/bin/activate
pip install -r requirements-dashboard.txt
deactivate
```

Update the restricted sudo rules because Phase 3 adds typed-confirm Pi reboot/shutdown:

```bash
sudo cp sudoers/raspberry-dashboard /etc/sudoers.d/raspberry-dashboard
sudo chmod 440 /etc/sudoers.d/raspberry-dashboard
sudo visudo -cf /etc/sudoers.d/raspberry-dashboard
```

It must say `parsed OK`.

Then restart only the dashboard:

```bash
sudo systemctl restart raspberry-dashboard
sudo systemctl status raspberry-dashboard
```

Health check:

```bash
curl http://127.0.0.1:8080/health
```

Expected:

```json
{"ok":true,"version":3}
```

Your existing Tailscale address and dashboard password continue to work.

## Safety boundaries

- `.env`, `.env.dashboard`, `.git`, `.venv`, `data/` and `logs/` remain blocked from the web code editor.
- The dashboard does not expose an arbitrary shell.
- Git discard refuses untracked files; delete those explicitly from the file manager if intended.
- Database restore creates a safety copy first and temporarily stops the bot.
- Reboot requires typing `REBOOT`; shutdown requires typing `SHUTDOWN`.
- Do not port-forward port 8080 to the public internet. Keep using Tailscale/LAN.
