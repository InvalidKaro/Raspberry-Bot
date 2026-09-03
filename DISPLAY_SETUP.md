# HomePi 0.96 OLED Display

Target hardware: 128x64 SSD1306 I2C OLED, blue pixels, Raspberry Pi 3 B+.

## Wiring

| OLED | Raspberry Pi 3 B+ |
|---|---|
| GND | Pin 6 (GND) |
| VCC | Pin 1 (3.3V) |
| SCL | Pin 5 / GPIO3 / SCL1 |
| SDA | Pin 3 / GPIO2 / SDA1 |

Use 3.3V for this setup.

## One-time installation

```bash
cd ~/services/Raspberry-Bot
git pull

sudo raspi-config
```

In `raspi-config`: **Interface Options -> I2C -> Enable**. Reboot if requested.

Then:

```bash
sudo apt update
sudo apt install -y i2c-tools python3-dev libjpeg-dev zlib1g-dev
sudo usermod -aG i2c stefano

cd ~/services/Raspberry-Bot
source .venv/bin/activate
pip install -r requirements-display.txt
deactivate

sudo cp systemd/raspberry-display.service /etc/systemd/system/raspberry-display.service
sudo systemctl daemon-reload
sudo systemctl enable --now raspberry-display
```

After adding the user to the `i2c` group, log out/in or reboot once before troubleshooting permissions.

## Check the display

```bash
i2cdetect -y 1
```

Normally the module appears at `3c`. If it appears at `3d`, select `0x3D` in Dashboard Pro -> Pi Display Builder.

Service status:

```bash
sudo systemctl status raspberry-display --no-pager
journalctl -u raspberry-display -n 80 --no-pager
```

Restart after an update:

```bash
sudo systemctl restart raspberry-display
```

## What the service shows

The physical display follows the Dashboard Pro configuration stored for guild `1162733312226361454`.

Base rotation always contains all pages:

1. Clock
2. Temperature + RAM
3. CPU + uptime
4. Network + Pi-hole
5. Now Playing

Media and high CPU/temperature can temporarily insert a priority page. They do not remove pages from the normal rotation.

The service reloads display configuration automatically, so most Builder changes do not require a service restart.

## Optional environment overrides

Create `.env.display` only when needed:

```env
DISPLAY_GUILD_ID=1162733312226361454
DISPLAY_DATABASE_PATH=/home/stefano/services/Raspberry-Bot/data/bot.sqlite3
DISPLAY_LOG_LEVEL=INFO
```
