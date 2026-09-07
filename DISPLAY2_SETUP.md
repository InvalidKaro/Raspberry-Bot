# HomePi Display 2 + Meshtastic

Display 2 is prepared for a second **0.96 inch 128x64 SSD1306 I2C OLED**. It intentionally uses a separate software I2C bus so both OLEDs may use the same fixed address `0x3C`.

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

## One-time install

```bash
cd ~/services/Raspberry-Bot
git pull
bash scripts/install_display2_service.sh
sudo reboot
```

After reboot:

```bash
sudo i2cdetect -y 1
sudo i2cdetect -y 3
```

It is valid for both buses to show `3c`:

```text
bus 1: 0x3C -> Display 1
bus 3: 0x3C -> Display 2
```

## Services

```text
raspberry-display.service      Display 1
raspberry-display2.service     Display 2
raspberry-meshtastic.service  Meshtastic USB collector
```

Display 2 and the Meshtastic collector are safe to install before their hardware exists. They stay in standby/retry mode instead of making the deployment fail.

## Display 2 pages

Normal rotation:

1. Meshtastic overview: online state, node count, received packet count, last activity
2. Last RF: RSSI, SNR and sender
3. Mesh nodes: known nodes and active nodes in the last 10 minutes / 1 hour
4. Services: bot, dashboard, Display 1 and Meshtastic service

A newly received Meshtastic text message temporarily overrides the normal rotation for 15 seconds and shows the sender and message.

## Meshtastic USB

The collector auto-detects one connected Meshtastic serial device by default.

If multiple serial devices are connected, set the device explicitly:

```bash
nano ~/services/Raspberry-Bot/.env.meshtastic
```

Example:

```env
MESHTASTIC_DEVICE=/dev/ttyACM0
```

Then:

```bash
sudo systemctl restart raspberry-meshtastic
```

## Diagnostics

```bash
sudo i2cdetect -y 3
sudo systemctl status raspberry-display2 --no-pager
sudo systemctl status raspberry-meshtastic --no-pager
cat ~/services/Raspberry-Bot/data/display2_status.json
cat ~/services/Raspberry-Bot/data/meshtastic_state.json
```

Preview without OLED:

```text
~/services/Raspberry-Bot/data/display2_preview.png
```
