# HomePi Dashboard / Raspberry-Bot — Phase 3.2

This package is **cumulative over Phase 3.1**. If Phase 3.1 has not been pushed yet, use this package instead and copy it over the existing repository.

## Main changes

### Correct CPU monitoring

- CPU sampling runs in the background instead of inside HTTP/Discord requests.
- Default sample interval: **15 seconds**.
- Allowed/configurable range: **10–30 seconds**.
- Dashboard shows:
  - latest system CPU sample
  - 30-second average
  - 5-minute rolling average
  - Raspberry-Bot CPU/RAM
  - Dashboard CPU/RAM
  - sample interval and sample age
  - network RX/TX rate
  - available RAM and swap
- Dashboard refresh remains every 30 seconds, while the sampler keeps measuring independently.

Optional dashboard environment setting:

```env
DASHBOARD_SAMPLE_INTERVAL=15
```

Optional bot environment setting:

```env
SYSTEM_METRICS_SAMPLE_INTERVAL=15
DASHBOARD_PORT=8080
```

These are optional because the defaults are already 15 seconds and port 8080.

### Pi-hole expansion

`/system pihole`, the Discord status buttons and the web dashboard now attempt to read:

- FTL service state
- blocking enabled/disabled
- query total
- blocked query total
- block percentage
- cached and forwarded queries
- unique domains
- clients
- gravity/blocklist domain count
- Core/Web/FTL versions

The implementation uses the local Pi-hole v6 CLI API (`pihole api ...`) and falls back to basic service status if detailed API access is unavailable.

Test detailed access manually with:

```bash
pihole api stats/summary
```

If Pi-hole asks for authentication and you want the bot user to use the local CLI API, follow your Pi-hole configuration for local CLI authentication. Do not put your regular Pi-hole password into the bot code.

### New/expanded Discord commands

See `docs/COMMANDS.md`. Major additions include:

- `/system health`
- `/system memory`
- `/system storage`
- `/system network` (owner)
- `/system processes` (owner)
- `/system pihole`
- `/system dashboard` (owner, ephemeral)
- `/system config`
- `/dev dashboard` (ephemeral)
- `/dev diagnostics`
- `/dev memory`
- `/dev extensions`
- `/dev load`, `/dev reload`, `/dev unload`
- `/dev logs`
- `/dev command-stats`
- `/dev database-stats`
- `/botinfo`
- `/permissions`
- `/commandinfo`
- `/invite`

Existing `/status`, `/ping`, `/userinfo`, `/serverinfo`, `/avatar`, `/roleinfo` and `/channelinfo` now return more information.

## Installation

Copy this ZIP over the existing local `Raspberry-Bot` repository, then commit and push.

On the Pi:

```bash
cd ~/services/Raspberry-Bot
git pull
source .venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-dashboard.txt
deactivate
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
{"ok": true, "version": "3.2"}
```

The first meaningful rolling CPU sample appears after roughly one sampling interval. After ~30 seconds the 30-second average is populated by multiple samples.

## Existing live Discord system monitor

New `/system setup` configurations default to a 30-second Discord message update interval. Existing database configurations keep their old interval intentionally. To change an existing monitor to 30 seconds, run `/system setup` again with `interval_seconds:30`.
