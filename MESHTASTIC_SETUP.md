# HomePi Meshtastic USB

Der Raspberry Pi 3 B+ nutzt den Meshtastic-Node per USB. Dadurch werden keine weiteren GPIO-Pins benötigt; USB liefert Strom und serielle Daten gleichzeitig.

## Erwartete USB-Schnittstelle

Das aktuelle LoRa-V3/CP2102-Board erscheint unter Linux typischerweise als:

```text
/dev/ttyUSB0
```

Je nach Board/Firmware kann stattdessen `/dev/ttyACM0` auftauchen. Prüfen mit:

```bash
lsusb
ls -l /dev/ttyUSB* /dev/ttyACM* 2>/dev/null
ls -l /dev/serial/by-id/ 2>/dev/null
```

Wenn `/dev/serial/by-id/...` vorhanden ist, ist dieser Pfad stabiler als `/dev/ttyUSB0` und kann in `.env.meshtastic` verwendet werden.

## Installation nur für Meshtastic

```bash
cd ~/services/Raspberry-Bot
git pull
bash scripts/install_meshtastic_service.sh
```

Der Installer:

1. fügt `stefano` zur Gruppe `dialout` hinzu,
2. installiert `requirements-meshtastic.txt` in die vorhandene `.venv`,
3. legt `.env.meshtastic` an,
4. installiert und startet `raspberry-meshtastic.service`.

## Gerät fest konfigurieren

Auto-Detect bleibt standardmäßig aktiv. Bei mehreren seriellen Geräten:

```bash
nano ~/services/Raspberry-Bot/.env.meshtastic
```

Beispiel für das CP2102-Board:

```env
MESHTASTIC_DEVICE=/dev/ttyUSB0
MESHTASTIC_RETRY_SECONDS=15
MESHTASTIC_LOG_LEVEL=INFO
MESHTASTIC_MAX_MESSAGES=25
```

Besser, falls vorhanden:

```env
MESHTASTIC_DEVICE=/dev/serial/by-id/usb-Silicon_Labs_CP2102_USB_to_UART_Bridge_Controller_...
```

Danach:

```bash
sudo systemctl restart raspberry-meshtastic
```

## Prüfung

```bash
sudo systemctl status raspberry-meshtastic --no-pager
journalctl -u raspberry-meshtastic -n 100 --no-pager
cat ~/services/Raspberry-Bot/data/meshtastic_state.json
```

Bei erfolgreicher Verbindung enthält der State unter anderem:

- USB-Gerät und Verbindungsstatus
- lokaler Node und Firmware
- Region, Modem-Preset, Primary Channel und TX-Status
- bekannte Nodes und Aktivität 10 min / 1 h
- RX-Paketanzahl
- letztes RSSI/SNR und Sender
- letzte Meshtastic-Nachricht
- begrenzten Nachrichtenverlauf

## Dashboard

Nach einem Update des Dashboard-Service:

```text
http://homepi.local:8080/meshtastic
```

Die Seite zeigt live:

- Verbindung und USB-Port
- Region / Preset / Primary Channel
- Node-Anzahl und aktive Nodes
- RSSI / SNR
- Node-Tabelle mit Hops, Akku und Hardware
- letzte Nachricht und Nachrichtenverlauf
- Status von Display 2

Die API dazu ist:

```text
/api/meshtastic
```

## Display 2

Display 2 liest denselben State aus `data/meshtastic_state.json`. Wenn Display 2 noch nicht installiert ist, kann Meshtastic trotzdem unabhängig laufen.

Für Display 2 anschließend:

```bash
bash scripts/install_display2_service.sh
sudo reboot
```

Details stehen in `DISPLAY2_SETUP.md`.

## Update auf dem Pi

Sobald die Änderungen auf `main` sind:

```bash
cd ~/services/Raspberry-Bot
bash scripts/update_pi.sh
```

Das Update-Script installiert auch die Meshtastic-Abhängigkeiten und startet `raspberry-meshtastic.service`, sofern der Dienst installiert ist.
