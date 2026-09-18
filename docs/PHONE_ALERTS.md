# HomePi automatic phone alerts

HomePi can place an outbound SIP call automatically when a critical condition
persists. Healthy operation is intentionally silent.

The alert monitor is a separate systemd service. It does not run inside the
Discord bot, so it can detect and report a crashed `raspberry-bot` service.

## What triggers a call

Default critical rules:

- CPU temperature at or above 80 C
- RAM at or above 95 percent
- root filesystem at or above 95 percent
- current Raspberry Pi undervoltage / throttling flags
- configured systemd services becoming offline

A condition must remain present for 90 seconds before it is eligible for a
call. The same alert key is rate-limited to one new call job per hour. Asterisk
itself retries an unanswered/failed call twice by default.

Normal operation generates no phone call.

## Architecture

```text
system metrics + systemd health
            |
            v
     persistence gate
            |
            v
 local espeak-ng voice
            |
            v
   Asterisk call file
            |
            v
      PJSIP provider
            |
            v
        telephone
```

The spoken text is generated locally with `espeak-ng`, then normalized to an
8 kHz mono WAV using SoX. No cloud TTS key is required.

## 1. Install

From the repository on the Pi:

```bash
cd /home/stefano/services/Raspberry-Bot
git pull --ff-only
sudo bash scripts/install_homepi_alerts.sh
```

The installer:

- installs Asterisk, espeak-ng and SoX
- creates a safe staging area for Asterisk call files
- installs and enables `homepi-alert-monitor.service`
- creates `.env.alerts` from the example if it does not already exist
- leaves automatic calls disabled by default

## 2. Configure a SIP provider

HomePi is provider-neutral. Use the SIP/PJSIP credentials supplied by your VoIP
provider.

A generic outbound example is stored at:

```text
asterisk/pjsip_homepi.example.conf
```

Do not commit real SIP credentials to Git.

Adapt the example to the provider's own documentation, then verify registration:

```bash
sudo asterisk -rx "pjsip show registrations"
```

The configured endpoint/trunk name must match
`HOMEPI_ALERT_PJSIP_TRUNK`. The default is `homepi-provider`.

Asterisk outbound registration and the endpoint used to place the call are
separate PJSIP objects, so both must be configured correctly.

## 3. Configure HomePi

Edit:

```bash
nano /home/stefano/services/Raspberry-Bot/.env.alerts
```

At minimum set:

```dotenv
HOMEPI_ALERT_TARGET=+49...
HOMEPI_ALERT_PJSIP_TRUNK=homepi-provider
HOMEPI_ALERTS_ENABLED=true
```

Use the destination number format required by the SIP provider.

Restart:

```bash
sudo systemctl restart homepi-alert-monitor
sudo systemctl status homepi-alert-monitor --no-pager
```

Follow logs:

```bash
journalctl -u homepi-alert-monitor -f
```

## Voice style

The default local speech settings are deliberately calm and technical:

```dotenv
HOMEPI_ALERT_VOICE=de
HOMEPI_ALERT_VOICE_SPEED=145
HOMEPI_ALERT_VOICE_PITCH=38
HOMEPI_ALERT_VOICE_AMPLITUDE=135
```

Example alert:

> Guten Abend. Ich muss Sie auf eine kritische Abweichung im HomePi System
> hinweisen. Die Prozessortemperatur liegt bei 83 Grad Celsius. Ich überwache
> die Situation weiter.

The system intentionally does not call when a problem is resolved or when all
monitored values are healthy.

## Anti-spam behavior

`HOMEPI_ALERT_CONFIRM_SECONDS` prevents short spikes from generating calls.

`HOMEPI_ALERT_MIN_INTERVAL_SECONDS` prevents the same alert from creating
continuous new calls. Asterisk's `MaxRetries`, `RetryTime` and `WaitTime`
handle attempts for the individual call job.

The last-alert timestamps are stored in the systemd state directory so a
watchdog restart does not immediately bypass the rate limit.

## Monitored services

The default list is:

```dotenv
HOMEPI_ALERT_MONITORED_SERVICES=raspberry-bot,raspberry-dashboard,pihole-FTL
```

Only add services that are actually installed and expected to remain running.
A missing configured unit is treated as offline.

## Safety / testing

Keep `HOMEPI_ALERTS_ENABLED=false` until PJSIP registration is working.

The monitor validates dial targets and trunk names before producing a call
file. Call files are fully written in a separate same-filesystem staging
directory and atomically moved into Asterisk's outgoing spool so Asterisk never
sees a partially written file.

Run the repository smoke test with:

```bash
python scripts/phone_alerts_smoke.py
```
