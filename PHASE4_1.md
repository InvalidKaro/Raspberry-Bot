# Raspberry-Bot 4.1

Performance, reporting and control-center release for Raspberry Pi hosts.

## Personnel 2.1

- `/perso leaderboard` ranks personnel by total activity, inductions or BWG.
- Optional period filter for leaderboard views.
- `/perso report` creates the complete personnel overview and activity diagram together.
- `/perso export` keeps overview PNG, chart PNG and CSV as separate export options.
- Pillow personnel renderer keeps a bounded scalable-font cache (`64`) to avoid unnecessary memory growth.
- Existing personnel rows and history remain persistent; 4.1 does not reset personnel data.

## Pi / memory optimization

- Persistent system-history sampling reduced from 30 seconds to 90 seconds.
- Pi-hole and Tailscale health checks are shared per sampling cycle instead of repeated per guild.
- Independent health checks run concurrently where possible.
- Historic snapshot cleanup is throttled instead of running on every sample.
- Existing eight-day monitoring retention is preserved.
- `/dev memory` reports bot RSS/VMS, host RAM, available memory, swap and thread count.
- `/dev performance` reports host/bot CPU, Discord latency, event-loop lag, SQLite latency, RAM, load and temperature.
- `/dev storage` reports disk, SQLite, WAL/SHM, logs and backup sizes.
- `/dev database-optimize` exposes safe SQLite `PRAGMA optimize` maintenance.
- `/dev diagnostics` combines service and system checks with concrete optimization recommendations.

## Control Center 4.1

- Live RAM, Bot RAM and Dashboard RAM metrics.
- CPU and recent CPU-average display.
- Temperature, swap, disk and bot-service health.
- Low-RAM health state with warnings for high RAM/swap usage.
- Smart health recommendations based on current Pi metrics.
- Recent RAM/CPU/temperature history charts.
- Personnel leaderboard directly in the Control Center.
- Personnel overview/chart PNG downloads using the same Pillow renderer as Discord.
- Safe maintenance actions are queued to the bot process: cache clear, Python GC and SQLite optimize.
- Existing Git pull, service restart, extension manager and command-sync controls remain available.

## Upgrade

```bash
cd ~/services/Raspberry-Bot
git pull
sudo systemctl restart raspberry-bot
sudo systemctl restart raspberry-dashboard
```

Run `/dev sync` once after the upgrade so the new application commands are visible immediately.
