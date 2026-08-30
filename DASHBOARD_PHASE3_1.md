# HomePi Dashboard 3.1 + Raspberry-Bot expansion

This update is a drop-in overlay for the existing `Raspberry-Bot` repository.

## Dashboard 3.1

- fixes NULL/default embed color handling
- saved Discord channel/role IDs are preserved and shown even if Discord cannot resolve them
- explicit config sync status: DB saved / bot restarted / DB reread verified
- shows the exact SQLite path used by the dashboard
- database path automatically follows `DATABASE_PATH` from the bot `.env` unless `BOT_DATABASE_PATH` explicitly overrides it
- read-only SQLite database browser
  - table list + row counts
  - search
  - pagination
  - table schema viewer
  - no raw SQL execution
  - no row writes/deletes from the browser
- welcome message editor
- automatic join role selector

## New bot features

### Persistent reminders

- `/reminder create`
- `/reminder list`
- `/reminder cancel`

Examples: `15m`, `2h`, `1d`, `1h30m`. Reminders survive bot restarts because they are stored in SQLite.

### Utility commands

- `/timestamp`
- `/snowflake`
- `/membercount`
- `/servericon`

### Server management

- `/manage role-add`
- `/manage role-remove`
- `/manage nickname`
- `/manage announce`

Discord permissions and role hierarchy are still enforced.

### Welcome / onboarding

- `/setup autorole`
- `/setup welcome-message`
- dashboard auto-role configuration
- dashboard custom welcome message

Welcome placeholders:

- `{user}`
- `{username}`
- `{display_name}`
- `{server}`
- `{member_count}`

## Install/update

After copying this package into the repository and pushing it to GitHub:

```bash
cd ~/services/Raspberry-Bot
git pull
```

Install dashboard dependencies (safe even if already installed):

```bash
source .venv/bin/activate
pip install -r requirements-dashboard.txt
pip install -r requirements.txt
deactivate
```

Restart **both** processes because this update changes the dashboard and the Discord bot:

```bash
sudo systemctl restart raspberry-bot
sudo systemctl restart raspberry-dashboard
```

Check:

```bash
sudo systemctl status raspberry-bot --no-pager
sudo systemctl status raspberry-dashboard --no-pager
curl http://127.0.0.1:8080/health
```

Expected dashboard health response:

```json
{"ok": true, "version": "3.1"}
```

If the bot fails after the update:

```bash
journalctl -u raspberry-bot -n 100 --no-pager
```

If the dashboard fails:

```bash
journalctl -u raspberry-dashboard -n 100 --no-pager
```

## Existing `#000000` value

Earlier dashboard builds could display an unset embed color as `#000000`. If `embed_color` is already stored as `0` in SQLite, Dashboard 3.1 treats that as an intentional black color because it cannot safely guess otherwise.

To return to the bot default color, open **Bot config → Embed color → Default → Save & apply**.
