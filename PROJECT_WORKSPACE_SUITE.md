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
- Smart Search across knowledge and training

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
- Message Composer
- Custom Command Builder
- Server Config UI
- Plugin UI
- Live Discord Console (audit + command analytics)
- Workspace status cards

### REST API
Read-only endpoints (protected by the dashboard's existing middleware):
- `/api/v1/status`
- `/api/v1/tasks`
- `/api/v1/events`
- `/api/v1/knowledge?q=...&kind=...`

## Main command groups
- `/creator ...`
- `/workspace ...`
- `/community ...`
- `/automation ...`

Existing `/perso`, ticket, moderation, Pi-hole/system and dashboard features remain separate.

## Database safety
All new functionality uses additive `CREATE TABLE IF NOT EXISTS` migrations. No existing Perso rows are deleted or rewritten by this project.
