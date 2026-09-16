from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


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

            result = self.devices.get(address)
            if result is None:
                result = GoveeBleDevice(
                    address=address,
                    name=name or "Govee BLE",
                    rssi=getattr(advertisement_data, "rssi", None),
                )
                self.devices[address] = result
            else:
                result.name = name or result.name
                result.rssi = getattr(advertisement_data, "rssi", result.rssi)
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

        return sorted(
            self.devices.values(),
            key=lambda item: (item.model or "", item.name, item.address),
        )

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
