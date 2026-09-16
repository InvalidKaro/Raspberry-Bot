# Govee Smart Home

Raspberry-Bot includes a lightweight local Govee integration for HomePi / Raspberry Pi 3 B+.
It does not require Home Assistant, Docker or Node-RED.

## Security scope

The `/home` command group and the `/climate` shortcut are registered only for Discord guild
`1162733312226361454`. The Cog checks the guild again for every command and interactive panel action.

All Smart Home responses are ephemeral. BLE MAC addresses are masked in Discord output; full addresses
remain local to HomePi where required for Bluetooth communication.

## Transports

### Govee LAN

Supported Govee LAN devices are discovered on the local network and controlled over the Govee LAN
protocol. The Pi may itself be connected over Wi-Fi; an Ethernet cable is not required.

For compatible Govee products, enable **LAN Control** in Govee Home.

The local protocol uses:

- discovery request: UDP 4001
- discovery response: UDP 4002
- control: UDP 4003

### Bluetooth Low Energy

The BLE radio is serialized through one asyncio lock so discovery, climate collection and active GATT
control do not fight over the Raspberry Pi's BlueZ adapter.

Observed devices in the HomePi environment:

| Model | Role in Raspberry-Bot | Transport |
| --- | --- | --- |
| H5075 | temperature/humidity/battery sensor + local history | passive BLE advertisement |
| H617E | power, brightness and RGB light control | active BLE GATT |
| H6076 | local light control when Govee LAN is available | LAN preferred |
| H6095 | local light control when Govee LAN is available | LAN preferred |

Direct BLE write support is allow-listed per model. Unknown Govee lights can be discovered, but the bot
will not send arbitrary GATT writes to them.

## Climate history and graphs

HomePi builds its own local climate history from H5075 advertisements.

- one short BLE scan every five minutes;
- current readings are also stored whenever `/climate` or `/home climate` is used;
- samples are stored in the existing SQLite database;
- history is retained for 31 days;
- no Govee cloud token is required;
- no permanent GATT connection is kept open.

The graph is rendered with Pillow on demand and uses the bot's existing image-render concurrency limit.
It contains separate temperature and humidity curves, current values, battery state, sample count and
Min / Average / Max statistics.

Supported graph periods:

- 6 hours
- 24 hours
- 7 days
- 30 days

The first invocation starts with the samples currently available. The graph becomes more detailed as the
five-minute collector builds local history.

This is HomePi-owned history. It does not scrape or copy the historical chart stored in the Govee mobile
app.

## Discord commands

- `/climate [period]` — shortcut that reads H5075 live and immediately sends the climate graph.
- `/home climate [period]` — same climate report inside the Smart Home command group.
- `/home scan` — refresh LAN + BLE discovery and show detected models.
- `/home status` — show controllable devices, transports, capabilities and climate sensors.
- `/home panel` — private device selector with On, Off, Night, Gaming and All Off.
- `/home power` — switch one selected LAN or supported BLE light.
- `/home brightness` — set brightness from 1 to 100 percent.
- `/home color` — set an RGB color.

Run `/home scan` before using light-control autocomplete after a bot restart.

## H617E BLE protocol

H617E control packets are exactly 20 bytes. Byte 0 is `0x33`; the final byte is an XOR checksum over the
preceding 19 bytes.

Raspberry-Bot uses the verified core commands:

- `0x01` — power
- `0x04` — brightness
- `0x05` with RGB payload — color

The driver only writes to the known Govee characteristics:

- `00010203-0405-0607-0809-0a0b0c0d2b11`
- `00010203-0405-0607-0809-0a0b0c0d1910`

It does not fall back to an arbitrary writable BLE characteristic.

Each active light command:

1. resolves the device through BLE discovery/cache;
2. connects with a bounded timeout;
3. selects a known writable characteristic;
4. sends one packet;
5. disconnects in `finally`;
6. retries once after a transient failure.

## Raspberry Pi setup

Bluetooth must be powered:

```bash
sudo systemctl enable --now bluetooth
sudo rfkill unblock bluetooth
bluetoothctl show
```

Expected:

```text
Powered: yes
PowerState: on
```

Install/update dependencies:

```bash
cd /home/stefano/services/Raspberry-Bot
source .venv/bin/activate
pip install -r requirements.txt
```

Restart:

```bash
sudo systemctl restart raspberry-bot
sudo systemctl status raspberry-bot --no-pager
```

## Troubleshooting

### `No powered Bluetooth adapters found`

```bash
bluetoothctl show
rfkill list bluetooth
sudo rfkill unblock bluetooth
bluetoothctl power on
```

### WLAN lights are missing

Confirm that:

- Pi and Govee device are on the same non-isolated home network;
- Govee Home has **LAN Control** enabled for the device;
- local UDP traffic on ports 4001–4003 is not blocked.

### BLE device is detected but not controllable

That is intentional unless its model is explicitly allow-listed. Discovery supports more Govee devices
than the active BLE light driver.

### H5075 is detected but has no reading

The H5075 publishes measurements through BLE advertisements. Run `/climate` again with the sensor in
range. The command uses a multi-second active scan window to catch its broadcast.

### Climate graph has only one or two points

That is expected immediately after deployment. HomePi adds a sample every five minutes, so a useful
24-hour curve builds automatically without keeping Bluetooth connected continuously.

## Resource behavior

The collector is intentionally conservative for Raspberry Pi 3 B+:

- climate BLE scan: about six seconds every five minutes;
- LAN discovery: only on discovery/refresh;
- BLE GATT connections: only for requested light-control actions;
- chart rendering: only when a climate command is requested;
- history retention: 31 days;
- no extra container or background web service.

This keeps the integration suitable for the same Pi that also runs Pi-hole, Raspberry-Bot and the other
HomePi services.
