# Phase 3.3.2 — Discord Bot Log Channel

Adds persistent owner-controlled Discord log forwarding.

## Commands

- `/botlog setup channel:#channel level:INFO`
- `/botlog status`
- `/botlog test`
- `/botlog disable`

## Behavior

- Mirrors Python/Raspberry-Bot logs into the configured Discord channel.
- Default minimum level is INFO.
- DEBUG, INFO, WARNING, ERROR and CRITICAL are selectable.
- Log lines are batched and sent silently.
- Mentions are disabled.
- Uses a bounded thread-safe queue so logging never blocks the bot.
- Persists configuration in SQLite (`discord_log_channels`).
- Excludes the Discord HTTP logger and the forwarding transport itself to avoid feedback loops.
