# Raspberry-Bot Commands — Phase 3.3.1

## Core / information

- `/help` — interactive help center
- `/ping` — gateway latency, uptime, guild count and runtime versions
- `/status` — HomePi status using the cached background CPU sampler
- `/botinfo` — uptime, guilds, members, commands, extensions and environment
- `/userinfo [member]` — detailed account/server membership and permissions
- `/serverinfo` — owner, member/channel split, boosts and features
- `/avatar [user]` — full-resolution avatar
- `/roleinfo <role>` — role details and key permissions
- `/channelinfo [channel]` — channel details
- `/permissions` — checks the bot's permissions in the current channel
- `/commandinfo <command_name>` — slash-command description and parameters
- `/invite` — private bot invite link
- `/timestamp`, `/snowflake`, `/membercount`, `/servericon`, `/profile`

## MD Personalabteilung statistics

- `/perso graph` — creates a Pillow graph from data entered directly in Discord
- `/perso list` — lists saved personnel datasets
- `/perso render <name>` — renders a saved dataset again
- `/perso delete <name>` — deletes a saved dataset
- `/perso help` — shows syntax/examples

Recommended input uses semicolons:

```text
Title: Bewerbungen pro Woche
X values: KW31;KW32;KW33;KW34
Y values: 12;18;15;23
X label: Kalenderwoche
Y label: Bewerbungen
Series name: Eingegangen
```

Optional comparison series:

```text
Second values: 8;13;11;19
Second series name: Eingestellt
```

`/perso graph` supports bar and line graphs, 1–24 data points, German decimal commas when semicolons are used, an optional private/ephemeral result and `save_as` for reusable datasets. The generated embed also calculates sum, average, median, min/max and first-to-last change.

## Welcome / onboarding

- `/setup welcome [channel]`
- `/setup welcome-message [message]`
- `/setup welcome-preview [member]`
- `/setup welcome-placeholders`
- `/setup autorole [role]`

Welcome templates support Dyno-style placeholders such as:

- `{user}` / `{user.mention}` — new member mention
- `{username}` / `{user.name}` — username
- `{display_name}` / `{user.display_name}` — server display name
- `{user.id}` — Discord user ID
- `{user.avatar}` — avatar URL
- `{user.created_at}` — account creation date
- `{user.joined_at}` — server join date
- `{user.top_role}` — highest role
- `{server}` / `{server.name}` — server name
- `{server.id}` — server ID
- `{server.owner}` — owner mention
- `{member_count}` — current member count
- `{channel}` / `{channel.name}` — welcome channel

Unknown placeholders remain visible in previews so typos are easy to spot.

## Raspberry Pi / HomePi

- `/system now`
- `/system health`
- `/system memory`
- `/system storage`
- `/system pihole`
- `/system graph [hours]`
- `/system setup`
- `/system config`
- `/system thresholds`
- `/system disable`

Owner-only:

- `/system dashboard` — ephemeral LAN + Tailscale dashboard links
- `/system network`
- `/system processes`

### Pi-hole behavior in Phase 3.3

If `/etc/pihole/pihole.toml` is not readable by the bot user, Raspberry-Bot no longer repeatedly invokes the Pi-hole CLI. It falls back to checking `pihole-FTL` through systemd and reports the permission limitation in `/system pihole`. This avoids repeated `pihole.toml ... Permission denied` journal lines.

## Developer / owner

Requires the Discord user ID in `OWNER_IDS`.

- `/dev dashboard`
- `/dev diagnostics`
- `/dev memory`
- `/dev extensions`
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

- `/ticket panel`, `/ticket info`, `/ticket queue`
- `/ticket claim`, `/ticket unclaim`, `/ticket priority`
- `/ticket add`, `/ticket remove`, `/ticket notes`
- `/ticket rename`, `/ticket transfer`, `/ticket transcript`
- `/ticket reopen`, `/ticket delete`

## Moderation

- `/mod warn`, `/mod warnings`, `/mod case`, `/mod unwarn`
- `/mod timeout`, `/mod untimeout`, `/mod kick`, `/mod ban`, `/mod unban`
- `/mod clear`, `/mod lock`, `/mod unlock`, `/mod slowmode`

## Community / management

- `/suggest`, `/poll`
- `/reminder create`, `/reminder list`, `/reminder cancel`
- `/manage role-add`, `/manage role-remove`, `/manage nickname`, `/manage announce`
- `/setup tickets`, `/setup staff-add`, `/setup staff-remove`
- `/setup suggestions`, `/setup logs`, `/setup show`

## MD Personalabteilung

- `/perso weekly` — fertige Wochenstatistik mit **Bewerbungen + Einweisungen**
- `/perso graph` — frei definierbare X-/Y-Achsen und optionale zweite Datenreihe
- `/perso list`, `/perso render`, `/perso delete`, `/perso help`

`/perso weekly` erwartet z. B. `KW35;KW36;KW37`, `12;17;14` und `5;8;6`. Der Bot berechnet zusätzlich Wochen-KPIs, Durchschnittswerte und den Quotienten Einweisungen/Bewerbungen.

## Phase 3.3 optimizations

- fixed duplicate `user_id` definition in the fresh-install `command_usage` schema
- added SQLite indexes for command history, reminders and personnel datasets
- SQLite uses WAL + `synchronous=NORMAL`, a small memory temp/cache budget and automatic WAL checkpoints
- image rendering remains semaphore-limited for Raspberry Pi 3 B+
- personnel graphs use Pillow only; no Matplotlib/NumPy dependency was added
- system CPU remains sampled in the background instead of inside dashboard requests
