# HomePi / Raspberry-Bot Audit

Stand: 2026-09-13

This document records the repository-wide audit performed for the Raspberry Pi 3 B+ deployment. It intentionally distinguishes implemented fixes from residual follow-up work so security and performance trade-offs stay visible.

## Priority findings

### HIGH — privileged systemd helper authorized arbitrary units — FIXED

`/usr/local/sbin/homepi-systemctl` is reachable through a narrow NOPASSWD sudo rule. The helper previously validated only unit-name syntax, which meant an authenticated caller could target unrelated systemd units.

Implemented:

- explicit HomePi unit allowlist in `scripts/homepi_systemctl.py`
- unknown units fail closed
- Discord service controls use the same finite HomePi service inventory
- Voice Control rejects arbitrary units such as `nginx`
- CI asserts that unknown units are rejected

### HIGH — Blackbox events had no retention — FIXED

`HOMEPI_BLACKBOX_RETENTION_DAYS` previously pruned samples but not events. On a long-running Raspberry Pi this could grow SQLite/WAL storage indefinitely.

Implemented:

- internal cleanup now prunes both `samples` and `events`
- daily hardened oneshot cleanup as a second safety net
- no scheduled `VACUUM`, avoiding unnecessary SD-card write amplification
- retention smoke coverage

### HIGH — vulnerable PyNaCl dependency line — FIXED

The `discord.py[voice]` extra resolved PyNaCl 1.5.0, while current security fixes require PyNaCl 1.6.2 or later.

Implemented:

- use base `discord.py`
- explicitly require `PyNaCl>=1.6.2,<2.0`
- keep `davey` explicitly declared
- `pip check` and `pip-audit` run in CI

### MEDIUM — inconsistent service/health logic — FIXED / CONSOLIDATED

Bot, dashboard, Voice Control and Intelligence had overlapping service knowledge.

Implemented:

- `services/health_checks.py`
- common `HealthResult`
- common HomePi service names for Bot, Dashboard, Radar, Mesh, Displays, Intelligence and Pi-hole
- shared argv-only async process runner
- Discord service status/restart chain uses the shared inventory

### MEDIUM — Server Score duplicated and spike-sensitive — FIXED

Implemented:

- shared weighted score in `services/server_score.py`
- CPU, RAM, temperature, disk, services, network and 24h stability components
- broad soft/hard ranges so a single CPU spike cannot collapse the score
- compatibility output preserves existing Intelligence fields (`system`, `network`, `services`, `stability`)
- Intelligence now consumes the shared calculation

### MEDIUM — excessive Intelligence SQLite writes — FIXED

The collector runs frequently, so writing unchanged state on every poll is expensive on an SD card.

Implemented:

- metadata UPSERT only writes when the value changes
- known-device `last_seen` is throttled instead of updated every collector cycle
- samples remain on their configured lower-frequency cadence
- Radar and Intelligence are now included in the default watched-service set

### MEDIUM — Voice Control per-client state could grow — FIXED

Implemented:

- bounded rate-bucket state
- expired confirmation cleanup
- maximum tracked-client state of 256 entries
- service help now lists only actually authorized HomePi units

### MEDIUM — Command Suggestions were static — FIXED

Implemented:

- central declarative Action Registry
- context-dependent actions
- permission filtering
- logical Service Control flow
- per-user interaction ownership
- `Manage Server` gate for restart actions
- second confirmation for restart

Current logical flow:

`Command -> contextual actions -> Services -> service select -> status -> Restart -> Confirm/Cancel`

## Raspberry Pi resource review

Already good and retained:

- bounded `TTLCache` usage
- rotating bot/error logs (5 MB, five backups)
- argv-based subprocess execution rather than shell concatenation
- bounded command timeouts in common process runner
- SQLite foreign keys and busy timeout in database-admin tooling
- backups before database-admin writes

Added:

- Blackbox retention timer
- lower SQLite write frequency
- shared health checks rather than duplicate polling implementations
- dependency audit gate

## Network-client review

Most outbound aiohttp paths already use explicit limits:

- Spotify runtime: explicit 15 s total / 5 s connect / 10 s read timeout
- Discord dashboard API: explicit 12 s total timeout
- weather/astronomy: explicit 12 s total timeout
- webhook automation: explicit 10 s total timeout
- Spotify playlist fallback: explicit connect/read/total limits
- radio metadata requests: explicit 8 s total / 3 s connect / 5 s read timeout

`media_interactive.py` owns a long-lived `ClientSession()` without a session-level timeout, but the actual radio metadata request supplies the explicit `REQUEST_TIMEOUT` above on every request. The session is also closed in `cog_unload()`. This is therefore a consistency cleanup, not an identified hanging-request or session-leak path.

## Security review

Verified / retained:

- `.env*` ignored, examples retained
- dashboard secrets configured through environment variables
- CSRF protection on authenticated dashboard mutations
- HttpOnly / SameSite session handling
- database-admin identifier validation and parameterized values
- primary-key requirement for database-admin update/delete operations
- no production `shell=True` hit found in repository search
- service log access is allowlisted

No hardcoded Discord/API token was found by repository code search. This is not equivalent to a full secret scan of every historical Git object; historical secret scanning should remain enabled in GitHub where available.

## Dependency audit

CI now runs `pip-audit` against production requirements.

One explicit exception remains:

- `PYSEC-2026-2132` — Click 8.1.8 via gTTS 2.5.x

Reason: current gTTS constrains Click below the fixed release line. HomePi imports gTTS as a Python library and does not execute its Click CLI surface. The exception is intentionally scoped to this one vulnerability and should be removed when gTTS permits a fixed Click version or TTS is migrated.

## Web redesign

Updated surfaces:

- HomePi Control Center
- Meshtastic dashboard
- public status page
- login page

Improvements include:

- common dark Mission-Control visual language
- clearer hierarchy
- responsive cards and controls
- keyboard focus states
- reduced-motion handling
- externalized public-status CSS/JS
- status DOM rendering through `textContent`/DOM nodes rather than raw HTML injection

The Flight Radar was audited and already had a mature dark radar/detail-panel layout, so it was not cosmetically rewritten merely to create diff volume.

## Existing safeguards deliberately kept

Database Admin already provides:

- validated table/column identifiers
- parameterized values
- foreign keys
- primary-key-only destructive row operations
- bounded safety backups

Logging already uses `RotatingFileHandler` for the primary and error logs.

The Flight Radar systemd unit already has useful hardening. Dashboard hardening cannot simply mirror Radar hardening because Dashboard intentionally performs Git/service-control operations.

## Residual follow-up items

These are known but intentionally not hidden behind a “complete” label:

1. **HTTP session consistency** — `media_interactive.py` can still be given the same session-level timeout as other long-lived clients. The actual radio request is already bounded per request, so this is low priority.
2. **Dashboard inline injection architecture** — `dashboard/__init__.py` still patches several pages with large inline navigation/debug fragments. This creates CSP exceptions and should be migrated to normal templates/static assets in a dedicated refactor.
3. **Login limiter key retention** — per-IP failure deques are individually bounded, but the IP-key map itself should receive periodic stale-key pruning for hostile/high-cardinality traffic.
4. **gTTS / Click advisory** — remove the documented audit exception as soon as upstream dependency constraints permit.
5. **Full Git-history secret scan** — code search found no embedded production token, but a dedicated historical scanner/host security feature is the correct mechanism for old commits.

These follow-ups should be addressed in isolated changes rather than mixed into a risky rewrite of the production dashboard runtime.

## CI / validation gate

The branch is expected to pass:

- Python 3.11 and 3.13 installation
- `pip check`
- `compileall`
- import smoke
- command hub smoke
- Control Center architecture smoke
- dashboard smoke
- MD plan smoke
- feature flags smoke
- display 1/2 smoke
- Meshtastic smoke
- HomePi Intelligence smoke
- Voice Control smoke
- privileged helper negative test
- Blackbox retention dry run
- installer shell syntax checks
- `pip-audit`

## Deployment notes

After merge/pull on the Pi, update the virtual environment so the PyNaCl security upgrade is applied:

```bash
cd /home/stefano/services/Raspberry-Bot
source .venv/bin/activate
pip install -r requirements.txt
pip check
```

Install/update Intelligence and Blackbox retention units:

```bash
bash scripts/install_homepi_intelligence.sh
```

Update the privileged helper if the Voice/Dashboard installer is not otherwise rerun:

```bash
sudo install -o root -g root -m 0755 scripts/homepi_systemctl.py /usr/local/sbin/homepi-systemctl
```

Then restart the relevant runtime services through the normal HomePi deployment procedure and verify:

```bash
sudo systemctl status raspberry-bot raspberry-dashboard raspberry-intelligence homepi-flight-radar --no-pager
sudo systemctl status raspberry-blackbox-prune.timer --no-pager
```
