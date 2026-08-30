# Raspberry-Bot Commands — Phase 3.2

## Core / information

- `/help` — interactive help center with expanded categories
- `/ping` — gateway latency, bot uptime, guild count and runtime versions
- `/status` — full HomePi status with 15-second cached CPU sampling and action buttons
- `/botinfo` — bot uptime, guilds, members, commands, extensions and environment
- `/userinfo [member]` — detailed account/server membership, roles and key permissions
- `/serverinfo` — owner, member split, channel split, boosts, verification and server features
- `/avatar [user]` — full-resolution avatar plus direct links
- `/roleinfo <role>` — members, color, position, creation time, flags and key permissions
- `/channelinfo [channel]` — category, creation time, slowmode, NSFW, sync and thread information
- `/permissions` — checks Raspberry-Bot permissions in the current channel
- `/commandinfo <command_name>` — description and parameters for a slash command
- `/invite` — private bot invite link with reviewed permissions
- `/timestamp`
- `/snowflake`
- `/membercount`
- `/servericon`
- `/profile [member]`

## Raspberry Pi / HomePi

- `/system now` — detailed system, process, Pi-hole and network status
- `/system health` — concise health checks and thresholds
- `/system memory` — RAM, available memory, swap, bot and dashboard RSS
- `/system storage` — root filesystem plus SQLite DB size/path
- `/system pihole` — detailed Pi-hole queries, blocking rate, clients, gravity and versions
- `/system graph [hours]` — Pillow history graph from 1 to 168 hours
- `/system setup <status_channel> [alert_channel] [interval_seconds]` — live status message; 15–300 seconds
- `/system config` — current monitor configuration and intervals
- `/system thresholds`
- `/system disable`

### Owner-only system tools

- `/system dashboard` — ephemeral LAN + Tailscale dashboard links
- `/system network` — traffic rates, interfaces and Tailscale IP
- `/system processes` — busiest host processes

## Developer / owner

Requires the Discord user ID in `OWNER_IDS`.

- `/dev dashboard` — ephemeral private dashboard links
- `/dev diagnostics` — DB, sampler, gateway and Pi-hole checks
- `/dev memory` — detailed Python process memory
- `/dev extensions` — configured/loaded extensions
- `/dev reload <extension>`
- `/dev load <extension>`
- `/dev unload <extension>`
- `/dev logs [lines]`
- `/dev command-stats`
- `/dev database-stats`
- `/dev database-optimize`
- `/dev cache-stats`
- `/dev cache-clear [cache_name]`
- `/dev gc`
- `/dev sync`

## Tickets

- `/ticket panel [channel]`
- `/ticket info`
- `/ticket queue`
- `/ticket claim`
- `/ticket unclaim`
- `/ticket priority`
- `/ticket add <member>`
- `/ticket remove <member>`
- `/ticket notes`
- `/ticket rename <name>`
- `/ticket transfer <staff-member>`
- `/ticket transcript`
- `/ticket reopen`
- `/ticket delete`

## Moderation

- `/mod warn`
- `/mod warnings`
- `/mod case`
- `/mod unwarn`
- `/mod timeout`
- `/mod untimeout`
- `/mod kick`
- `/mod ban`
- `/mod unban`
- `/mod clear`
- `/mod lock`
- `/mod unlock`
- `/mod slowmode`

## Community

- `/suggest`
- `/poll`
- `/reminder create`
- `/reminder list`
- `/reminder cancel`

## Server management

- `/manage role-add`
- `/manage role-remove`
- `/manage nickname`
- `/manage announce`

## Configuration

- `/setup tickets`
- `/setup staff-add`
- `/setup staff-remove`
- `/setup welcome`
- `/setup welcome-message`
- `/setup autorole`
- `/setup suggestions`
- `/setup logs`
- `/setup show`

## Monitoring behavior

The CPU value is no longer measured inside the command/dashboard request. Raspberry-Bot and the web dashboard each keep a lightweight background sampler. Default sampling is every **15 seconds**, configurable between **10 and 30 seconds**.

This prevents the dashboard request itself from creating misleading 60–90% CPU spikes.
