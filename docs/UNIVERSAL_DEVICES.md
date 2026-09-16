# Universal Device Hub

Raspberry-Bot can discover and control local devices independently of manufacturer. A device is only writable when a known local protocol is positively identified and its capabilities are derived safely.

## Discord commands

The command group is guild-scoped to `1162733312226361454`:

- `/devices scan [mode]` — discover devices and evaluate controllability.
- `/devices list` — show the current registry.
- `/devices inspect <device>` — explain protocol, capabilities and why control is or is not available.
- `/devices power <device> <state>` — vendor-neutral on/off control.
- `/devices brightness <device> <value>` — vendor-neutral brightness control.
- `/devices color <device> <r> <g> <b>` — vendor-neutral RGB control.

`Schnell` uses current LAN neighbors, mDNS, SSDP and BLE. `Tiefenscan` additionally probes the Pi's local IPv4 /24 segment (or a /24 slice of a larger subnet) for supported local APIs.

## Control states

A discovered device gets one of four states:

1. `controllable` — a supported local protocol was identified and safe capabilities are known.
2. `auth_required` — the protocol is known but credentials are required.
3. `pairing_required` — the protocol is known but a pairing/controller flow is required.
4. `detected_only` — the device is visible but Raspberry-Bot does not have a safe write driver for it.

Unknown devices are never written to. Generic BLE devices are observation-only unless an explicit driver exists.

## Implemented adapters

### Govee

The existing Govee LAN and allow-listed BLE drivers are exposed through the universal registry. The H5075 remains read-only as a climate sensor.

### WLED

Detection: local `/json/info` endpoint.

Supported writes through `/json/state`:

- power
- brightness
- RGB

Effects are detected as a capability and can be exposed later without changing the discovery architecture.

### Shelly Gen2+

Detection: local `/shelly` endpoint followed by `Shelly.GetStatus` when authentication is disabled.

Supported components:

- `switch:<id>` — power
- `light:<id>` — power, brightness
- `rgb:<id>` — power, brightness, RGB

If authentication is enabled the device is reported as `auth_required`; credentials are not guessed or stored automatically. Shelly Gen1 is detected but currently left read-only.

### Tasmota

Detection: local HTTP `Status 0` command.

Capabilities are enabled only when the status payload reports them:

- `POWER*` -> power
- `Dimmer` -> brightness
- `Color` -> RGB

Protected instances are reported as requiring authentication.

## Discovery-only protocols

The mDNS scanner also recognizes protocols that need pairing or an authenticated controller layer before safe writes are possible:

- HomeKit Accessory Protocol (`_hap._tcp`)
- Matter operational/commissionable services
- ESPHome Native API

These devices appear in `/devices list` and `/devices inspect`, but write commands remain disabled until a dedicated authenticated adapter is configured.

## Bluetooth behavior

The universal scanner performs one bounded BLE discovery window during a device scan. It shares the existing Govee BLE radio lock so climate collection, Govee GATT writes and generic discovery do not fight over BlueZ on the Pi 3 B+.

Named non-Govee BLE devices are listed as observed devices. Anonymous BLE advertisements are counted but not dumped into Discord. MAC addresses shown in Discord are masked.

## Resource behavior

The hub is designed for Raspberry Pi 3 B+:

- no always-on generic network scanner;
- no permanent generic BLE loop;
- quick scan uses existing neighbor state plus multicast discovery;
- deep scan is user-triggered;
- HTTP probes have short timeouts and bounded concurrency;
- no Home Assistant, Docker or Node-RED dependency.

## Adding another manufacturer

Add a positive-identification adapter rather than vendor conditionals in Discord commands. A driver should:

1. identify a device without changing its state;
2. report canonical protocol/vendor/model data;
3. derive capabilities from the device rather than assumptions;
4. require credentials or pairing when the protocol requires them;
5. implement only verified write operations;
6. never fall back to arbitrary BLE GATT writes or undocumented network commands.

This keeps `/devices power`, `/devices brightness` and `/devices color` vendor-neutral while drivers remain isolated and auditable.
