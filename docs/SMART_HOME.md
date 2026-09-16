# Govee Smart Home

Raspberry-Bot includes a lightweight local Govee integration for the HomePi.
It is intentionally designed for a Raspberry Pi 3 B+: there is no Home
Assistant, Docker, Node-RED or always-on Bluetooth polling requirement.

## Security scope

The `/home` command group is registered only for Discord guild
`1162733312226361454`. The Cog also checks the guild again for every command
and every interactive panel action.

All `/home` responses are ephemeral. BLE MAC addresses are masked in Discord
output; full addresses remain internal selectors only.

## Transports

### Govee LAN

Supported Govee LAN devices are discovered on the local network and controlled
over the Govee LAN protocol. The Pi may itself be connected over Wi-Fi; an
Ethernet cable is not required.

For compatible Govee products, enable **LAN Control** in Govee Home.

The implementation uses the local UDP ports used by the Govee LAN protocol:

- discovery request: UDP 4001
- discovery response: UDP 4002
- control: UDP 4003

### Bluetooth Low Energy

Bluetooth scanning is on demand. The bot does not keep a background BLE scan
or permanent GATT connection alive.

The BLE radio is serialized through one asyncio lock so discovery and active
control do not fight over the Raspberry Pi's BlueZ adapter.

Observed devices in the HomePi environment:

| Model | Role in Raspberry-Bot | Transport |
| --- | --- | --- |
| H5075 | temperature/humidity/battery sensor | passive BLE advertisement |
| H617E | power, brightness and RGB light control | active BLE GATT |
| H6076 | local light control when Govee LAN is available | LAN preferred |
| H6095 | local light control when Govee LAN is available | LAN preferred |

Direct BLE write support is deliberately allow-listed per model. Unknown Govee
lights may be discovered by `/home scan`, but the bot will not send arbitrary
GATT writes to them.

## Discord commands

- `/home scan` — refresh LAN + BLE discovery and show detected models.
- `/home status` — show controllable devices, transports, capabilities and
  climate sensors.
- `/home panel` — private device selector with On, Off, Night, Gaming and
  All Off actions.
- `/home climate` — actively scan for Govee climate advertisements and show
  decoded temperature, humidity and battery values.
- `/home power` — switch one selected LAN or supported BLE light.
- `/home brightness` — set brightness from 1 to 100 percent.
- `/home color` — set an RGB color.

Run `/home scan` before using slash-command autocomplete after a bot restart.

## H617E BLE protocol

H617E control packets are exactly 20 bytes. Byte 0 is `0x33`; the final byte is
an XOR checksum over the preceding 19 bytes.

Raspberry-Bot currently uses the verified core command surface only:

- `0x01` — power
- `0x04` — brightness
- `0x05` with RGB payload — color

The driver only writes to the known Govee characteristics:

- `00010203-0405-0607-0809-0a0b0c0d2b11`
- `00010203-0405-0607-0809-0a0b0c0d1910`

It does not fall back to an arbitrary writable BLE characteristic.

Each command:

1. resolves the device through BLE discovery;
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

Install/update bot dependencies:

```bash
cd /home/stefano/services/Raspberry-Bot
source .venv/bin/activate
pip install -r requirements.txt
```

Restart the service:

```bash
sudo systemctl restart raspberry-bot
sudo systemctl status raspberry-bot --no-pager
```

## Troubleshooting

### `No powered Bluetooth adapters found`

Check:

```bash
bluetoothctl show
rfkill list bluetooth
```

Then:

```bash
sudo rfkill unblock bluetooth
bluetoothctl power on
```

### WLAN lights are missing

Confirm:

- Pi and Govee device are on the same non-isolated home network;
- Govee Home has **LAN Control** enabled for the device;
- local UDP traffic on ports 4001–4003 is not blocked.

### BLE device is detected but not controllable

That is intentional unless its model is explicitly allow-listed. `/home scan`
can discover more Govee products than the active BLE driver supports.

### H5075 is detected but has no reading

The H5075 publishes measurements through BLE advertisements. Run
`/home climate` again with the sensor in range; the command uses an active scan
window long enough to catch its measurement broadcast.

## Resource behavior

There is no permanent scan loop. Idle overhead is limited to Python objects
already loaded by the Discord bot.

- LAN discovery: only on discovery/refresh.
- BLE discovery: only on discovery, climate reads or device resolution when the
  cache is empty.
- BLE connections: only for a requested control action.
- Cached BLE entries expire from normal device listings after five minutes.

This keeps the integration suitable for the same Raspberry Pi 3 B+ that also
runs Pi-hole, the Discord bot and the existing HomePi services.
