# HomePi Dashboard — Phase 2

Phase 2 upgrades the existing dashboard with:

- responsive polished control UI
- repository file browser and code editor
- Python syntax validation before save
- protected paths (`.env`, `.git`, `.venv`, `data`, `logs` cannot be edited)
- Git diff, selected-file commit, pull and push
- Discord guild/channel/role discovery through the bot token (token stays server-side)
- ticket/welcome/suggestions/audit settings in the web UI
- Save & Apply restarts Raspberry-Bot so cached settings are immediately fresh
- deploy preflight via `compileall`, restart and service health check
- explicit rollback button to the commit stored before the last dashboard deploy

## Update an existing Phase 1 installation

Merge this ZIP into the root of `Raspberry-Bot`, commit and push it from your PC. Then on the Pi:

```bash
cd ~/services/Raspberry-Bot
git pull
source .venv/bin/activate
pip install -r requirements-dashboard.txt
deactivate
sudo cp systemd/raspberry-dashboard.service /etc/systemd/system/raspberry-dashboard.service
sudo cp sudoers/raspberry-dashboard /etc/sudoers.d/raspberry-dashboard
sudo chmod 440 /etc/sudoers.d/raspberry-dashboard
sudo visudo -cf /etc/sudoers.d/raspberry-dashboard
sudo systemctl daemon-reload
sudo systemctl restart raspberry-dashboard
```

No new secret is required if your existing `.env.dashboard` already works. The new `BOT_ENV_PATH` and `BOT_DATABASE_PATH` values are optional because the dashboard defaults to `.env` and `data/bot.sqlite3` inside the repo.

Check:

```bash
curl http://127.0.0.1:8080/health
sudo systemctl status raspberry-dashboard
```

Expected health response contains `"version": 2`.

## Important safety behavior

- The code editor never exposes `.env` or database/log files.
- It only edits existing text/code files up to 512 KiB.
- Python files are parsed before they are saved.
- `git pull` refuses to run with local uncommitted changes.
- Commit only stages files selected in the dashboard.
- Rollback refuses to run while uncommitted changes exist.
- No arbitrary shell endpoint is included.
- Keep TCP/8080 private to LAN/Tailscale; do not port-forward it publicly.
