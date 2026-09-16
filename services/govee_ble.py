from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

_MODEL_RE = re.compile(r"(H[0-9A-Z]{4})", re.IGNORECASE)


@dataclass(slots=True)
class GoveeBleDevice:
    address: str
    name: str
    rssi: int | None
    model: str | None = None
    readings: dict[str, float | int | str | bool | None] = field(default_factory=dict)
    last_seen: float = field(default_factory=time.monotonic)

    @property
    def temperature_c(self) -> float | None:
        return self._numeric_reading("temperature")

    @property
    def humidity_percent(self) -> float | None:
        return self._numeric_reading("humidity")

    @property
    def battery_percent(self) -> float | None:
        return self._numeric_reading("battery")

    @property
    def display_name(self) -> str:
        return f"{self.model or 'Govee'} · {self.name}"

    def _numeric_reading(self, needle: str) -> float | None:
        for key, value in self.readings.items():
            if needle in key.lower() and isinstance(value, (int, float)):
                return float(value)
        return None


class GoveeBleScanner:
    def __init__(self) -> None:
        self.devices: dict[str, GoveeBleDevice] = {}

    @staticmethod
    def _looks_like_govee(name: str) -> bool:
        lowered = name.lower()
        return (
            "govee" in lowered
            or lowered.startswith("gvh")
            or lowered.startswith("gv")
            or lowered.startswith("ihoment")
        )

    @staticmethod
    def infer_model(name: str) -> str | None:
        match = _MODEL_RE.search(name.upper())
        return match.group(1).upper() if match else None

    async def scan(self, timeout: float = 5.0) -> list[GoveeBleDevice]:
        try:
            from bleak import BleakScanner
            from bluetooth_sensor_state_data import BluetoothServiceInfo
            from govee_ble import GoveeBluetoothDeviceData
        except ImportError as exc:
            raise RuntimeError(
                "BLE-Unterstützung fehlt. Installiere `bleak` und `govee-ble` "
                "aus requirements.txt."
            ) from exc

        parsers: dict[str, Any] = {}

        def detection_callback(device: Any, advertisement_data: Any) -> None:
            name = str(
                getattr(advertisement_data, "local_name", None)
                or getattr(device, "name", None)
                or ""
            ).strip()
            address = str(getattr(device, "address", "") or "").strip()
            if not address or not self._looks_like_govee(name):
                return

            inferred_model = self.infer_model(name)
            result = self.devices.get(address)
            if result is None:
                result = GoveeBleDevice(
                    address=address,
                    name=name or "Govee BLE",
                    rssi=getattr(advertisement_data, "rssi", None),
                    model=inferred_model,
                )
                self.devices[address] = result
            else:
                result.name = name or result.name
                result.rssi = getattr(advertisement_data, "rssi", result.rssi)
                result.model = inferred_model or result.model
                result.last_seen = time.monotonic()

            parser = parsers.setdefault(address, GoveeBluetoothDeviceData())
            try:
                service_info = BluetoothServiceInfo(
                    name=name or result.name,
                    address=address,
                    rssi=int(getattr(advertisement_data, "rssi", -127)),
                    manufacturer_data=dict(
                        getattr(advertisement_data, "manufacturer_data", {}) or {}
                    ),
                    service_uuids=list(
                        getattr(advertisement_data, "service_uuids", []) or []
                    ),
                    service_data=dict(
                        getattr(advertisement_data, "service_data", {}) or {}
                    ),
                    source="local",
                )
                update = parser.update(service_info)
                result.model = getattr(parser, "device_type", None) or result.model
                self._merge_readings(result, update)
            except Exception:
                logger.debug(
                    "Govee BLE advertisement was not a sensor packet: %s (%s)",
                    name,
                    address,
                    exc_info=True,
                )

        scanner = BleakScanner(
            detection_callback=detection_callback,
            scanning_mode="active",
        )
        try:
            await scanner.start()
            await asyncio.sleep(max(1.0, min(timeout, 15.0)))
        finally:
            await scanner.stop()

        return self.cached_devices()

    @staticmethod
    def _merge_readings(device: GoveeBleDevice, update: Any) -> None:
        descriptions = getattr(update, "entity_descriptions", {}) or {}
        values = getattr(update, "entity_values", {}) or {}

        for description in descriptions.values():
            device_key = getattr(description, "device_key", None)
            if device_key is None:
                continue
            value = values.get(device_key)
            if value is None:
                continue

            native_value = getattr(value, "native_value", None)
            key_obj = getattr(device_key, "key", "")
            key = str(key_obj or "").strip()
            device_class = getattr(description, "device_class", None)
            if hasattr(device_class, "value"):
                device_class = device_class.value
            label = " ".join(
                part
                for part in (
                    str(device_class or "").strip(),
                    key,
                    str(getattr(description, "name", "") or "").strip(),
                )
                if part
            ).strip()
            if not label:
                label = key or "value"
            device.readings[label] = native_value

    def cached_devices(self) -> list[GoveeBleDevice]:
        return sorted(
            self.devices.values(),
            key=lambda item: (item.model or "", item.name, item.address),
        )

    def resolve(self, selector: str) -> GoveeBleDevice:
        value = selector.strip().lower()
        if not value:
            raise LookupError("Kein Bluetooth-Gerät angegeben.")

        exact: list[GoveeBleDevice] = []
        partial: list[GoveeBleDevice] = []
        for device in self.devices.values():
            fields = (
                device.address,
                device.name,
                device.model or "",
            )
            lowered = tuple(field.lower() for field in fields)
            if value in lowered:
                exact.append(device)
            elif any(value in field for field in lowered):
                partial.append(device)

        matches = exact or partial
        if not matches:
            raise LookupError("Bluetooth-Gerät nicht gefunden. Führe zuerst `/home scan` aus.")
        if len(matches) > 1:
            raise LookupError("Mehrere Bluetooth-Geräte passen. Nutze die Autovervollständigung.")
        return matches[0]
