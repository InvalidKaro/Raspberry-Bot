# Raspberry-Bot

A modular Discord community-management bot designed to run efficiently on a Raspberry Pi 3 B+ alongside Pi-hole.

The project is built around `discord.py`, async SQLite, persistent Discord UI components, lightweight Raspberry Pi monitoring, Pillow-based graphs/profile cards, ticket workflows, moderation cases, suggestions, polls, server configuration and owner maintenance tools.

## Highlights

- Modern slash commands and Discord UI: buttons, selects and modals
- Consistent embed design through a central `EmbedFactory`
- Persistent ticket controls that survive bot restarts
- Ticket claim/unclaim, priorities, transfers, internal notes, extra members, close/reopen/delete and HTML transcripts
- Multiple configurable ticket staff roles
- Moderation case database: warnings, timeouts, kicks, bans and channel tools
- Raspberry Pi monitoring: CPU, temperature, RAM, storage, load, network, throttling flags, uptime and Pi-hole FTL status
- One live Discord status message that is edited instead of spamming a channel
- Alert channel for temperature, RAM, disk and throttling conditions
- 24-hour Pillow system-health graph
- Pillow profile cards with a render semaphore to protect low-memory hosts
- Suggestions with persistent voting and approve/deny controls
- Interactive polls
- Welcome messages
- Per-guild configuration stored in SQLite
- TTL cache manager with manual clearing and automatic expiry
- Rotating logs and SQLite WAL mode
- `systemd` deployment template for automatic startup/restart

## Recommended host

The project is intentionally conservative enough for a Raspberry Pi 3 B+ with Raspberry Pi OS Lite and Pi-hole. Pillow work is performed on demand and limited by `IMAGE_RENDER_CONCURRENCY`, which defaults to `1`.

## 1. Create the Discord application

1. Open the Discord Developer Portal.
2. Create a new application and add a bot user.
3. Under **Privileged Gateway Intents**, enable:
   - Server Members Intent
   - Message Content Intent
4. Under OAuth2 / URL Generator, use scopes:
   - `bot`
   - `applications.commands`
5. Give the bot the permissions required by the features you intend to use. For the full feature set this normally includes View Channels, Send Messages, Embed Links, Attach Files, Read Message History, Manage Channels, Manage Messages, Moderate Members, Kick Members and Ban Members.
6. Keep the bot role above members it needs to moderate.

Never commit the bot token to GitHub.

## 2. Configure the project

Copy the example environment file:

```bash
cp .env.example .env
```

Edit `.env`:

```env
DISCORD_TOKEN=your_real_bot_token
OWNER_IDS=your_discord_user_id
DEV_GUILD_ID=your_test_server_id
BOT_NAME=Raspberry-Bot
ENVIRONMENT=production
DATABASE_PATH=data/bot.sqlite3
LOG_LEVEL=INFO
DEFAULT_EMBED_COLOR=5793266
SYSTEM_MONITOR_INTERVAL=300
SYSTEM_TEMP_WARNING=70
SYSTEM_TEMP_CRITICAL=80
SYSTEM_RAM_WARNING=80
SYSTEM_DISK_WARNING=85
TRANSCRIPT_MAX_MESSAGES=3000
IMAGE_RENDER_CONCURRENCY=1
```

`DEV_GUILD_ID` is optional. During development it makes command synchronization much faster because commands are synchronized directly to one server.

## 3. Install on Raspberry Pi

From the Raspberry Pi:

```bash
mkdir -p ~/services
cd ~/services
git clone https://github.com/YOUR-NAME/Raspberry-Bot.git
cd Raspberry-Bot
chmod +x scripts/install_pi.sh scripts/update_pi.sh
./scripts/install_pi.sh
```

Then create `.env` and start the bot for the first test:

```bash
source .venv/bin/activate
python bot.py
```

## 4. Configure the Discord server

Recommended order:

1. `/setup tickets` — choose the ticket category and ticket log channel.
2. `/setup staff-add` — add one or more ticket staff roles.
3. `/ticket panel` — post the persistent support panel.
4. `/setup welcome` — optional welcome channel.
5. `/setup suggestions` — optional suggestions channel.
6. `/setup logs` — optional audit-log channel for messages, members, voice and channel changes.
7. `/system setup` — choose the live Raspberry Pi status channel and optional alert channel.
8. `/system thresholds` — optionally tune temperature/RAM/disk alert limits.

## 5. Run as a systemd service

Copy the provided unit file and adjust the username/path if necessary:

```bash
sudo cp systemd/raspberry-bot.service /etc/systemd/system/raspberry-bot.service
sudo systemctl daemon-reload
sudo systemctl enable --now raspberry-bot
```

Useful commands:

```bash
sudo systemctl status raspberry-bot
sudo systemctl restart raspberry-bot
sudo journalctl -u raspberry-bot -f
```

## Updates

On the Raspberry Pi:

```bash
cd ~/services/Raspberry-Bot
./scripts/update_pi.sh
```

The update script pulls with fast-forward only, updates dependencies and restarts the service when it is installed.

## Pi-hole integration

Raspberry-Bot does not change Pi-hole configuration. The system monitor only checks whether `pihole-FTL.service` is active. This keeps the bot isolated from your DNS configuration and means a bot error cannot alter Pi-hole settings.

## Data and logs

Runtime data is intentionally excluded from Git:

- `.env`
- `data/bot.sqlite3`
- SQLite WAL/SHM files
- `logs/`

SQLite uses WAL mode, foreign keys and a busy timeout. Application logs use rotating files to avoid unlimited SD-card growth.

## Architecture

```text
Discord
  │
  ├── Cogs / slash commands
  ├── Views / buttons / selects
  └── Modals
       │
       ▼
Services
  ├── Tickets
  ├── Cache / maintenance
  ├── Raspberry Pi metrics
  ├── Pillow charts/cards
  └── Transcripts
       │
       ▼
Repositories / Database
       │
       ▼
SQLite
```

See [`docs/COMMANDS.md`](docs/COMMANDS.md) for the included command set.

## Resource design

The bot is built for small hardware:

- No Docker requirement
- No heavyweight plotting stack such as Matplotlib
- Pillow rendering is on demand and semaphore-limited
- Status monitoring updates one existing message
- Metric history is pruned automatically after 31 days
- TTL caches are bounded by maximum size
- Logs rotate automatically
- SQLite is asynchronous through `aiosqlite`

## Security notes

- Keep `.env` private.
- Use a private GitHub repository while developing if that is easier.
- Do not grant Administrator unless you deliberately want the bot to have unrestricted server permissions.
- Developer commands require a user ID listed in `OWNER_IDS`.
- Ticket staff access is based on configured Discord roles plus server administrator/owner privileges.

## License

MIT. See `LICENSE`.
