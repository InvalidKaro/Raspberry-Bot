from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from services.govee_ble import GoveeBleDevice

logger = logging.getLogger(__name__)


class GoveeBleControlError(RuntimeError):
    """Raised when an explicitly supported BLE light cannot be controlled."""


@dataclass(frozen=True, slots=True)
class GoveeBleLightCapabilities:
    power: bool
    brightness: bool
    rgb: bool


class GoveeBleLightController:
    """Direct BLE controller for explicitly supported Govee light models.

    BLE sessions are short-lived and serialized through one shared radio lock.
    This keeps the Raspberry Pi 3 B+ idle footprint low and avoids scan/connect
    collisions on BlueZ.
    """

    WRITE_UUIDS = (
        "00010203-0405-0607-0809-0a0b0c0d2b11",
        "00010203-0405-0607-0809-0a0b0c0d1910",
    )
    SUPPORTED_MODELS = {
        "H617E": GoveeBleLightCapabilities(
            power=True,
            brightness=True,
            rgb=True,
        ),
    }

    def __init__(self, radio_lock: asyncio.Lock | None = None) -> None:
        self._radio_lock = radio_lock or asyncio.Lock()

    @classmethod
    def capabilities_for(
        cls,
        device: GoveeBleDevice,
    ) -> GoveeBleLightCapabilities | None:
        return cls.SUPPORTED_MODELS.get((device.model or "").upper())

    @classmethod
    def supports(cls, device: GoveeBleDevice) -> bool:
        return cls.capabilities_for(device) is not None

    @staticmethod
    def _build_packet(command: int, payload: list[int]) -> bytes:
        data = [0x33, command, *payload]
        if len(data) > 19:
            raise ValueError("Govee BLE packet payload is too large")
        data.extend([0x00] * (19 - len(data)))

        checksum = 0
        for byte in data:
            checksum ^= byte
        data.append(checksum)

        packet = bytes(data)
        if len(packet) != 20:
            raise AssertionError("Govee BLE packets must be exactly 20 bytes")
        return packet

    @classmethod
    def _power_packet(cls, on: bool) -> bytes:
        return cls._build_packet(0x01, [0x01 if on else 0x00])

    @classmethod
    def _brightness_packet(cls, percent: int) -> bytes:
        value = max(0, min(100, int(percent)))
        encoded = round(value / 100 * 0xFE)
        return cls._build_packet(0x04, [encoded])

    @classmethod
    def _color_packet(cls, r: int, g: int, b: int) -> bytes:
        rgb = [
            max(0, min(255, int(r))),
            max(0, min(255, int(g))),
            max(0, min(255, int(b))),
        ]
        return cls._build_packet(
            0x05,
            [0x15, 0x01, *rgb, 0, 0, 0, 0, 0, 0xFF, 0x7F],
        )

    async def power(self, device: GoveeBleDevice, on: bool) -> None:
        self._validate(device, "power")
        await self._write(device, self._power_packet(on))

    async def brightness(self, device: GoveeBleDevice, percent: int) -> None:
        self._validate(device, "brightness")
        await self._write(device, self._brightness_packet(percent))

    async def color(self, device: GoveeBleDevice, r: int, g: int, b: int) -> None:
        self._validate(device, "rgb")
        await self._write(device, self._color_packet(r, g, b))

    def _validate(self, device: GoveeBleDevice, capability: str) -> None:
        model = (device.model or "").upper()
        capabilities = self.SUPPORTED_MODELS.get(model)
        if capabilities is None:
            raise GoveeBleControlError(
                f"Direkte Bluetooth-Steuerung ist für {model or device.name} "
                "noch nicht freigeschaltet."
            )
        if not bool(getattr(capabilities, capability, False)):
            raise GoveeBleControlError(
                f"{model} unterstützt diese BLE-Funktion im Bot nicht."
            )

    async def _write(self, device: GoveeBleDevice, packet: bytes) -> None:
        try:
            from bleak import BleakClient, BleakScanner
        except ImportError as exc:
            raise GoveeBleControlError("`bleak` ist nicht installiert.") from exc

        last_error: Exception | None = None
        async with self._radio_lock:
            for attempt in range(1, 3):
                client: Any | None = None
                try:
                    ble_device = await BleakScanner.find_device_by_address(
                        device.address,
                        timeout=5.0,
                    )
                    if ble_device is None:
                        raise GoveeBleControlError(
                            f"{device.model or 'Govee'} ist aktuell nicht in Bluetooth-Reichweite."
                        )

                    client = BleakClient(ble_device, timeout=12.0)
                    await client.connect()
                    if not client.is_connected:
                        raise GoveeBleControlError(
                            f"Keine Bluetooth-Verbindung zu {device.model or device.name}."
                        )

                    characteristic = self._find_write_characteristic(client)
                    if characteristic is None:
                        raise GoveeBleControlError(
                            f"Keine bekannte Govee-Schreib-Characteristic bei "
                            f"{device.model or device.name} gefunden."
                        )

                    properties = set(getattr(characteristic, "properties", []) or [])
                    response = (
                        "write" in properties
                        and "write-without-response" not in properties
                    )
                    await client.write_gatt_char(
                        characteristic,
                        packet,
                        response=response,
                    )
                    logger.info(
                        "Govee BLE command sent model=%s address=%s attempt=%s",
                        device.model or "unknown",
                        device.masked_address,
                        attempt,
                    )
                    return
                except Exception as exc:
                    last_error = exc
                    logger.warning(
                        "Govee BLE command attempt failed model=%s address=%s attempt=%s error=%s",
                        device.model or "unknown",
                        device.masked_address,
                        attempt,
                        type(exc).__name__,
                    )
                    if attempt < 2:
                        await asyncio.sleep(0.6)
                finally:
                    if client is not None and client.is_connected:
                        try:
                            await client.disconnect()
                        except Exception:
                            logger.debug(
                                "Govee BLE disconnect failed model=%s address=%s",
                                device.model or "unknown",
                                device.masked_address,
                                exc_info=True,
                            )

        if isinstance(last_error, GoveeBleControlError):
            raise last_error
        if last_error is not None:
            raise GoveeBleControlError(
                f"Bluetooth-Steuerung von {device.model or device.name} ist nach "
                f"2 Versuchen fehlgeschlagen ({type(last_error).__name__})."
            ) from last_error
        raise GoveeBleControlError("Bluetooth-Steuerung ist fehlgeschlagen.")

    def _find_write_characteristic(self, client: object) -> Any | None:
        services = getattr(client, "services", None)
        if services is None:
            return None

        wanted = {uuid.lower() for uuid in self.WRITE_UUIDS}
        for service in services:
            for characteristic in service.characteristics:
                properties = set(getattr(characteristic, "properties", []) or [])
                writable = (
                    "write" in properties
                    or "write-without-response" in properties
                )
                if writable and str(characteristic.uuid).lower() in wanted:
                    return characteristic
        return None
