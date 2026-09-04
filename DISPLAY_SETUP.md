# HomePi 0.96 Blue OLED

Target hardware: **0.96 inch, 128x64, SSD1306, I2C, blue pixels** on the Raspberry Pi 3 B+.

The display service is intentionally deployable **before the hardware exists**. Without an OLED it runs in `standby`, keeps reading the Dashboard Pro configuration, writes a headless preview PNG, and retries the I2C device automatically.

## Wiring

| OLED | Raspberry Pi 3 B+ |
|---|---|
| GND | Pin 6 (GND) |
| VCC | Pin 1 (3.3V) |
| SCL | Pin 5 / GPIO3 / SCL1 |
| SDA | Pin 3 / GPIO2 / SDA1 |

Use **3.3V** for this setup.

## Deploy now, even without the OLED

```bash
cd ~/services/Raspberry-Bot
git pull
bash scripts/install_display_service.sh
```

The installer:

1. installs the I2C/display dependencies,
2. enables I2C when `raspi-config` is available,
3. installs `requirements-display.txt` into the existing venv,
4. runs a deployment check,
5. installs and enables `raspberry-display.service`.

No connected OLED is required for a successful deployment.

## Expected status before the hardware arrives

```bash
sudo systemctl status raspberry-display --no-pager
cat ~/services/Raspberry-Bot/data/display_status.json
```

Expected state:

```json
{
  "mode": "standby",
  "hardware_connected": false,
  "hardware_optional": true
}
```

The service stays healthy. It retries the OLED every 20 seconds instead of crashing or spamming a traceback.

A software preview is continuously written to:

```text
~/services/Raspberry-Bot/data/display_preview.png
```

Manual deployment check:

```bash
cd ~/services/Raspberry-Bot
.venv/bin/python -m display_service.main --check
```

Hardware missing is still exit code `0` while `DISPLAY_ALLOW_MISSING_HARDWARE=1`.

## When the OLED is connected later

Check the bus:

```bash
i2cdetect -y 1
```

Normally the device appears at `3c`. If it appears at `3d`, select `0x3D` in Dashboard Pro -> Pi Display Builder.

The already-running service automatically retries the device and switches from `standby` to `hardware` when it becomes reachable. A restart is optional:

```bash
sudo systemctl restart raspberry-display
```

## What the physical display shows

The service follows the Dashboard Pro layout for guild `1162733312226361454` and cycles through every core page:

1. Clock
2. Temperature + RAM
3. CPU + uptime
4. Network + Pi-hole
5. Now Playing

Every page remains in the normal rotation. Media start or high CPU/temperature may temporarily show the relevant page early, but the base rotation continues afterwards.

The service reloads the saved Dashboard Pro display configuration automatically.

## Logs and diagnostics

```bash
journalctl -u raspberry-display -n 80 --no-pager
sudo systemctl restart raspberry-display
.venv/bin/python -m display_service.main --check
```

The current runtime status is also available in:

```text
data/display_status.json
```

## Optional `.env.display`

Copy the example only when overrides are needed:

```bash
cp .env.display.example .env.display
```

Default deploy-safe values include:

```env
DISPLAY_GUILD_ID=1162733312226361454
DISPLAY_ALLOW_MISSING_HARDWARE=1
DISPLAY_HARDWARE_RETRY_SECONDS=20
DISPLAY_HEADLESS_PREVIEW=1
```

Set `DISPLAY_ALLOW_MISSING_HARDWARE=0` only if you later want systemd startup to fail when the OLED cannot be reached.
