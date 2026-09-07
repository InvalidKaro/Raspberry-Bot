# HomePi Display 2 — Meshtastic + adaptive fallback

Display 2 is a second **0.96 inch 128x64 SSD1306 I2C OLED** on its own software-I2C bus. It now has two automatic profiles:

- **Meshtastic connected:** dedicated mesh/RF pages.
- **Meshtastic disconnected:** a full HomePi operations rotation that intentionally avoids duplicating Display 1.

Priority overlays can temporarily interrupt either profile for new mesh messages, voice/system actions and important HomePi alerts.

## Wiring

Display 1 stays unchanged on the normal Raspberry Pi I2C bus.

Display 2 defaults to:

| OLED 2 | Raspberry Pi 3 B+ |
|---|---|
| GND | physical pin 20 (GND) |
| VCC | physical pin 17 (3.3V) |
| SDA | physical pin 16 / GPIO23 |
| SCL | physical pin 18 / GPIO24 |

Use **3.3V**.

The installer adds:

```ini
dtoverlay=i2c-gpio,bus=3,i2c_gpio_sda=23,i2c_gpio_scl=24
```

This creates `/dev/i2c-3` after reboot.

## Install / update

```bash
cd ~/services/Raspberry-Bot
git pull --ff-only
bash scripts/install_display2_service.sh
```

If Voice Control is already installed, reinstall its root-owned helper once after this update so Display 2 receives voice/systemctl result events:

```bash
bash scripts/install_voice_control.sh
```

Then:

```bash
sudo systemctl restart raspberry-display2
```

A reboot is only required when the software-I2C overlay was added for the first time.

## Automatic profile: Meshtastic connected

Rotation:

1. **MESHTASTIC** — online state, known nodes, RX count, recent activity.
2. **LAST RF** — RSSI, SNR and sender.
3. **MESH NODES** — known/active nodes over 10 minutes and 1 hour.

A newly received Meshtastic text message temporarily overrides the rotation.

## Automatic profile: Meshtastic disconnected

Display 2 becomes an operations display instead of repeating Display 1.

### 1. Discord Bot

Shows:

- bot service online/offline
- connected guild/runtime count
- commands in the last 24 hours
- command errors in the last 24 hours
- open tickets
- Discord latency/member count when runtime telemetry provides them

### 2. Pi-hole statistics

Shows real Pi-hole summary data when the local Pi-hole CLI/API is readable:

- queries
- blocked queries
- blocked percentage
- active clients

If Pi-hole statistics are permission-limited, service state still remains available.

### 3. Live network traffic

Shows the preferred active interface with:

- RX bytes/second
- TX bytes/second
- LAN IPv4 address
- link speed

This uses local psutil counters and does not run a speed test.

### 4. Git / deployment status

Shows:

- current branch
- short commit SHA
- clean/dirty checkout state
- commits ahead/behind the configured upstream

The ahead/behind value uses the locally known upstream ref. Display 2 intentionally does **not** run `git fetch` in the background.

### 5. Storage / database

Shows:

- root filesystem usage
- SQLite database size
- `/var/log` size
- backup count and total backup size

### 6. Remote access

Shows:

- Tailscale state
- Tailscale IPv4 address
- SSH state
- LAN IP
- public IPv4 address when available

Public-IP lookup is cached for 10 minutes. Disable it completely with:

```env
DISPLAY2_PUBLIC_IP=0
```

### 7. Internet health

Every 30 seconds the cached health collector checks:

- ICMP latency to `1.1.1.1`
- DNS lookup latency
- online/offline state

This is intentionally lightweight and is not a bandwidth/speed test.

### 8. HomePi activity feed

Shows recent events collected from existing local data:

- Dashboard/Discord activity
- audit actions
- recent Discord commands
- latest Voice/systemctl event
- latest Meshtastic RX packet

## Voice/system action overlay

The root-owned `homepi-systemctl` helper now writes a tiny event file after privileged system actions. Display 2 shows it briefly, for example:

```text
VOICE        OK
raspberry-bot.service
RESTART
```

The event is stored at:

```text
~/services/Raspberry-Bot/data/voice_display_event.json
```

The helper never writes the Voice API token into this file.

## Alert overlay

Important conditions temporarily take priority:

- Raspberry-Bot service offline
- Internet offline
- root filesystem at least 90% full
- at least 5 command errors in 24 hours
- local checkout behind its known upstream

Alerts are shown briefly instead of permanently blocking the normal rotation.

## Load on Raspberry Pi 3 B+

The expensive/slow collectors are cached:

| Collector | Approx. refresh |
|---|---:|
| Network RX/TX | 3 s |
| Discord/DB counters | 10 s |
| Tailscale/SSH | 15 s |
| Pi-hole | 30 s |
| Git | 30 s |
| Internet ping/DNS | 30 s |
| Storage/log sizes | 60 s |
| Public IP | 10 min |

There is no second speech recognizer, no continuous speed test and no periodic `git fetch`.

## Optional `.env.display2`

Example:

```env
DISPLAY2_PAGE_SECONDS=5
DISPLAY2_REFRESH_SECONDS=3
DISPLAY2_MESSAGE_SECONDS=15
DISPLAY2_VOICE_SECONDS=12
DISPLAY2_ALERT_SECONDS=15
DISPLAY2_PUBLIC_IP=1
```

## Diagnostics

```bash
sudo i2cdetect -y 3
sudo systemctl status raspberry-display2 --no-pager
journalctl -u raspberry-display2 -n 100 --no-pager
cat ~/services/Raspberry-Bot/data/display2_status.json
cat ~/services/Raspberry-Bot/data/meshtastic_state.json
cat ~/services/Raspberry-Bot/data/voice_display_event.json 2>/dev/null
```

Headless preview:

```text
~/services/Raspberry-Bot/data/display2_preview.png
```

`display2_status.json` contains the currently selected `profile` (`mesh` or `homepi`) and current page.
