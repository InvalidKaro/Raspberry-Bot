# Raspberry-Bot Workspace Suite

This project bundle expands Raspberry-Bot into a modular Discord workspace. It intentionally excludes RP/patient/funk generators.

## Included

### Creator
- Announcement builder
- Embed builder
- Persistent message templates
- Dynamic forms (up to 5 modal fields) + response storage
- Button panels for role/info/link actions
- Persistent role-select menus

### Workspace
- Weekly planner generator
- Event manager + RSVP
- Task board
- Combined calendar overview
- Reminder Hub view
- Training library
- Question/quiz pool
- Medication/knowledge entries
- Internal wiki
- FAQ system
- Ranked Smart Search with autocomplete across knowledge, training, quiz, templates, forms and custom commands

### Community
- Lightweight XP with a 45-second per-user write throttle
- Levels + leaderboard
- Default and custom achievements
- Quote system
- Giveaways with persistent join buttons and automatic ending

### Automation
- Dashboard/Discord Custom Command Builder (`!name`)
- Message scheduler
- Template scheduler
- Webhook scheduler
- Plugin enable/disable state persisted across restarts
- Webhook Hub
- Background scheduler runner

### Dashboard
- `/workspace` Control Center
- `/workspace/studio` Workspace Studio
- Message Composer
- Rich Embed Builder + live Discord-style preview
- Template auto-fill
- Smart Search + live suggestions
- Workspace data catalog
- Custom Command Builder
- Server Config UI
- Plugin UI
- Live Discord Console (audit + command analytics)
- Workspace status cards

## Dashboard architecture

The dashboard uses authenticated internal `/api/workspace/...` routes to communicate with its own backend. There is intentionally no separate public `/api/v1/...` REST API surface. Bot actions that must execute inside the Discord process are queued through `dashboard_commands`.

## Main command groups
- `/creator ...`
- `/workspace ...`
- `/community ...`
- `/automation ...`
- `/mdplan ...`

Existing `/perso`, ticket, moderation, Pi-hole/system and dashboard features remain separate.

## Database safety
All new functionality uses additive `CREATE TABLE IF NOT EXISTS` migrations. No existing Perso rows are deleted or rewritten by this project.
