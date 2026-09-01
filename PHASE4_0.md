# Raspberry-Bot 4.0

Major management release.

Implemented:
- Perso 2.0 persistent staff profiles and history, `/perso add`, `/perso record`, `/perso overview`, `/perso person`, `/perso compare`, PNG/CSV export.
- Ticket feedback and statistics on top of existing claim/unclaim/priority/transfer/member/note/transcript/reopen workflow.
- Moderation history and escalation helper on top of case IDs, warnings, timeout/ban history.
- Discord log routing with separate INFO/WARNING/ERROR channels and error-ID embeds.
- Onboarding role buttons, rule confirmation, welcome preview/placeholders.
- 30-second historic system snapshots with Pi-hole/Tailscale health.
- Automated SQLite optimize/checkpoint and backup rotation (7 daily, 4 weekly, 10 manual).
- Bot access roles: ticketstaff/perso/moderator/admin.
- Persistent audit trail and command analytics.
- Maintenance mode and `/dev doctor`.
- Dashboard command queue for live cog load/reload/unload and command sync.
- Dashboard Control Center UI + API with live cog load/reload/unload, command sync and ticket/personnel/mod/error/backup totals.
- Owner backup/restore/health tools.

After upgrading, restart the bot once to apply the new SQLite schema, then run `/dev sync`.
