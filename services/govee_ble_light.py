from __future__ import annotations

import asyncio
from dataclasses import dataclass

from services.govee_ble import GoveeBleDevice


@dataclass(frozen=True, slots=True)
class GoveeBleLightCapabilities:
    power: bool
    brightness: bool
    rgb: bool


class GoveeBleLightController:
    """Direct BLE controller for explicitly supported Govee light models.

    Connections are short-lived and opened only for a requested command so the
    Raspberry Pi does not keep BLE sessions alive while idle.
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

    def __init__(self) -> None:
        self._lock = asyncio.Lock()

    @classmethod
    def supports(cls, device: GoveeBleDevice) -> bool:
        return (device.model or "").upper() in cls.SUPPORTED_MODELS

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
        return bytes(data)

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
            raise RuntimeError(
                f"Direkte Bluetooth-Steuerung ist für {model or device.name} noch nicht freigeschaltet."
            )
        if not bool(getattr(capabilities, capability, False)):
            raise RuntimeError(f"{model} unterstützt diese BLE-Funktion im Bot nicht.")

    async def _write(self, device: GoveeBleDevice, packet: bytes) -> None:
        try:
            from bleak import BleakClient
        except ImportError as exc:
            raise RuntimeError("`bleak` ist nicht installiert.") from exc

        async with self._lock:
            client = BleakClient(device.address, timeout=15.0)
            try:
                await client.connect()
                if not client.is_connected:
                    raise ConnectionError(f"Keine BLE-Verbindung zu {device.display_name}")

                characteristic = self._find_write_characteristic(client)
                if characteristic is None:
                    raise RuntimeError(
                        f"Keine bekannte Govee-Schreib-Characteristic bei {device.display_name} gefunden."
                    )

                await client.write_gatt_char(characteristic, packet, response=False)
            finally:
                if client.is_connected:
                    await client.disconnect()

    def _find_write_characteristic(self, client: object) -> str | None:
        services = getattr(client, "services", None)
        if services is None:
            return None

        wanted = {uuid.lower() for uuid in self.WRITE_UUIDS}
        for service in services:
            for characteristic in service.characteristics:
                uuid = str(characteristic.uuid).lower()
                properties = set(characteristic.properties or [])
                if uuid in wanted and (
                    "write" in properties or "write-without-response" in properties
                ):
                    return str(characteristic.uuid)
        return None
