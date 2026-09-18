# HomePi Phone Assistant

HomePi can automatically place an outbound SIP call when a critical condition
persists. Healthy operation is silent.

The assistant runs as a separate systemd watchdog, not inside the Discord bot.
It can therefore detect a crashed `raspberry-bot` service and still place the
alert call.

## Final behavior

Default critical conditions:

- CPU temperature at or above 80 C
- RAM at or above 95 percent
- root filesystem at or above 95 percent
- current Raspberry Pi undervoltage / throttling
- configured systemd services becoming offline

A condition must persist for 90 seconds before the first call is created.
Short spikes do not call.

When the call is answered, HomePi uses an interactive Asterisk AGI session:

```text
HomePi alert
    |
    +-- 1  acknowledge the incident
    +-- 2  read current CPU temperature / RAM / disk / affected service status
    +-- 3  safely restart the affected service when it is allowlisted
    +-- 9  repeat the incident
    +-- 0  end the call
```

If the incident remains critical and is not acknowledged, HomePi queues another
escalation after five minutes. Two escalation calls are allowed by default.
Asterisk also keeps its normal retry behavior for an individual failed or
unanswered call.

Acknowledged or resolved incidents are closed and are not escalated.

## Security model

The telephone path never receives arbitrary shell access.

Interactive restart requests are written to a shared Asterisk/HomePi spool.
The independent HomePi monitor validates them and calls the existing
`/usr/local/sbin/homepi-systemctl` helper.

The one-time installer creates exact sudo rules only for:

- `raspberry-bot.service`
- `raspberry-dashboard.service`
- `pihole-FTL.service`

The helper has its own fixed service allowlist as a second authorization layer.
A telephone caller cannot supply a custom command or custom systemd unit.

## Architecture

```text
system metrics + systemd health
             |
             v
       persistence gate
             |
             v
         incident store
             |
             v
       local TTS / WAV
             |
             v
        Asterisk call
             |
             v
        PJSIP provider
             |
             v
           phone
             |
             v
      Asterisk AGI menu
        |     |      |
        |     |      +--> allowlisted restart request
        |     +---------> live system status
        +---------------> acknowledgement
```

The speech path has no required cloud TTS dependency. The base installation uses
`espeak-ng` and SoX, so the alert still has a local fallback voice.

## One-time installation

Do this after the feature is on `main`:

```bash
cd /home/stefano/services/Raspberry-Bot
git switch main
git pull --ff-only

sudo bash scripts/install_homepi_alerts.sh
```

That single installer:

- installs Asterisk
- installs espeak-ng and SoX
- creates the shared incident/action/audio spool
- installs the Asterisk AGI assistant
- installs/refreshes the root-owned HomePi systemctl validator
- installs the exact phone-assistant sudo rules
- installs and enables `homepi-alert-monitor.service`
- creates `.env.alerts` if it does not exist
- migrates an older `.env.alerts` with the new interactive defaults
- leaves outbound automatic calls disabled until SIP is configured

## SIP / PJSIP provider

HomePi is provider-neutral. Use the PJSIP credentials supplied by the selected
VoIP provider.

A generic outbound template is included at:

```text
asterisk/pjsip_homepi.example.conf
```

Do not commit real SIP credentials.

After configuring Asterisk, verify registration:

```bash
sudo asterisk -rx "pjsip show registrations"
```

The Asterisk endpoint name must match:

```dotenv
HOMEPI_ALERT_PJSIP_TRUNK=homepi-provider
```

## HomePi configuration

Edit:

```bash
nano /home/stefano/services/Raspberry-Bot/.env.alerts
```

Minimum configuration:

```dotenv
HOMEPI_ALERT_TARGET=+49...
HOMEPI_ALERT_PJSIP_TRUNK=homepi-provider
HOMEPI_ALERTS_ENABLED=true
```

Recommended interactive defaults:

```dotenv
HOMEPI_ALERT_INTERACTIVE=true
HOMEPI_ALERT_ESCALATION_SECONDS=300
HOMEPI_ALERT_MAX_ESCALATIONS=2
HOMEPI_ALERT_ACTION_TIMEOUT_SECONDS=30
HOMEPI_ALERT_RESTARTABLE_SERVICES=raspberry-bot.service,raspberry-dashboard.service,pihole-FTL.service
```

Restart after editing:

```bash
sudo systemctl restart homepi-alert-monitor
sudo systemctl status homepi-alert-monitor --no-pager
```

## Doctor check

Run before the first real call:

```bash
cd /home/stefano/services/Raspberry-Bot
.venv/bin/python scripts/homepi_alertctl.py doctor
```

It checks:

- configured target number
- PJSIP trunk name
- Asterisk
- espeak-ng
- SoX
- AGI installation
- required spool directories
- Asterisk registration output

If the installer just added your login to the `asterisk` group, start a new
SSH/login session before running the CLI as your normal user.

## Explicit test call

Once the SIP registration works:

```bash
cd /home/stefano/services/Raspberry-Bot
.venv/bin/python scripts/homepi_alertctl.py test-call
```

The test call explicitly says that no real system fault exists. In interactive
mode it also exercises the AGI keypad menu.

## Normal alert example

A real alert may start with:

> Guten Abend. Ich muss Sie auf eine kritische Abweichung im HomePi System
> hinweisen. Der Dienst raspberry bot ist nicht erreichbar. Ich überwache die
> Situation weiter.

The assistant then presents the interactive menu.

## Escalation and anti-spam

`HOMEPI_ALERT_CONFIRM_SECONDS` filters brief spikes.

`HOMEPI_ALERT_MIN_INTERVAL_SECONDS` prevents the same alert key from creating
continuous new incidents.

`HOMEPI_ALERT_ESCALATION_SECONDS` controls the delay before an unacknowledged,
still-active incident is called again.

`HOMEPI_ALERT_MAX_ESCALATIONS` controls how many additional incident calls are
allowed after the first one.

Asterisk's `MaxRetries`, `RetryTime` and `WaitTime` apply separately to
each queued call attempt.

## Logs

Monitor:

```bash
journalctl -u homepi-alert-monitor -f
```

Asterisk:

```bash
sudo journalctl -u asterisk -f
```

Current assistant installation:

```bash
.venv/bin/python scripts/homepi_alertctl.py doctor
```

## Development validation

Repository smoke tests:

```bash
python scripts/phone_alerts_smoke.py
python scripts/phone_assistant_smoke.py
bash -n scripts/install_homepi_alerts.sh
```

CI runs the phone tests on Python 3.11 and Python 3.13.
