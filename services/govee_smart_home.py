from __future__ import annotations

import asyncio
from dataclasses import dataclass

from services.govee_ble import (
    GoveeBleDevice,
    GoveeBleScanError,
    GoveeBleScanner,
    GoveeBleUnavailableError,
)
from services.govee_ble_light import GoveeBleLightController
from services.govee_lan import GoveeLanClient, GoveeLanDevice


@dataclass(slots=True)
class GoveeDiscovery:
    lan: list[GoveeLanDevice]
    ble: list[GoveeBleDevice]
    lan_error: str | None = None
    ble_error: str | None = None


@dataclass(frozen=True, slots=True)
class GoveeControlResult:
    transport: str
    display_name: str


@dataclass(frozen=True, slots=True)
class GoveeDeviceSummary:
    selector: str
    display_name: str
    model: str
    transport: str
    capabilities: tuple[str, ...]
    detail: str


@dataclass(frozen=True, slots=True)
class GoveeBatchResult:
    applied: int
    failed: int
    errors: tuple[str, ...]


class GoveeSmartHomeService:
    """Low-overhead local Govee controller for LAN and supported BLE models."""

    def __init__(self) -> None:
        self._ble_radio_lock = asyncio.Lock()
        self.lan = GoveeLanClient()
        self.ble = GoveeBleScanner(self._ble_radio_lock)
        self.ble_lights = GoveeBleLightController(self._ble_radio_lock)
        self._discover_lock = asyncio.Lock()

    async def discover_all(
        self,
        *,
        lan_timeout: float = 2.5,
        ble_timeout: float = 6.0,
    ) -> GoveeDiscovery:
        async with self._discover_lock:
            lan_result, ble_result = await asyncio.gather(
                self.lan.discover(lan_timeout),
                self.ble.scan(ble_timeout),
                return_exceptions=True,
            )

        lan_error = None
        ble_error = None

        if isinstance(lan_result, Exception):
            lan: list[GoveeLanDevice] = []
            lan_error = self._short_error(lan_result)
        else:
            lan = lan_result

        if isinstance(ble_result, Exception):
            ble: list[GoveeBleDevice] = []
            ble_error = self._short_error(ble_result)
        else:
            ble = ble_result

        if isinstance(lan_result, Exception) and isinstance(ble_result, Exception):
            raise RuntimeError(
                f"WLAN/LAN: {lan_error or 'Fehler'}; Bluetooth: {ble_error or 'Fehler'}"
            )

        return GoveeDiscovery(
            lan=lan,
            ble=ble,
            lan_error=lan_error,
            ble_error=ble_error,
        )

    async def ensure_lan_devices(self) -> list[GoveeLanDevice]:
        cached = self.lan.cached_devices()
        if cached:
            return cached
        return await self.lan.discover()

    async def ensure_ble_devices(self) -> list[GoveeBleDevice]:
        cached = self.ble.cached_devices()
        if cached:
            return cached
        return await self.ble.scan(6.0)

    async def refresh_devices(self) -> GoveeDiscovery:
        return await self.discover_all()

    def controllable_devices(self) -> list[GoveeDeviceSummary]:
        devices: list[GoveeDeviceSummary] = []

        for device in self.lan.cached_devices():
            devices.append(
                GoveeDeviceSummary(
                    selector=f"lan:{device.device_id}",
                    display_name=f"{device.sku} · WLAN",
                    model=device.sku,
                    transport="WLAN/LAN",
                    capabilities=("power", "brightness", "rgb"),
                    detail=device.ip,
                )
            )

        for device in self.ble.cached_devices():
            capabilities = self.ble_lights.capabilities_for(device)
            if capabilities is None:
                continue

            supported = tuple(
                name
                for name in ("power", "brightness", "rgb")
                if bool(getattr(capabilities, name, False))
            )
            devices.append(
                GoveeDeviceSummary(
                    selector=f"ble:{device.address}",
                    display_name=f"{device.model or 'Govee'} · Bluetooth",
                    model=device.model or "Govee",
                    transport="Bluetooth",
                    capabilities=supported,
                    detail=device.masked_address,
                )
            )

        return sorted(
            devices,
            key=lambda item: (item.model, item.transport, item.display_name),
        )

    def sensor_devices(self) -> list[GoveeBleDevice]:
        return [
            device
            for device in self.ble.cached_devices()
            if device.temperature_c is not None or device.humidity_percent is not None
        ]

    async def power_device(self, selector: str, on: bool) -> GoveeControlResult:
        transport, target = await self._resolve_control_target(selector)
        if transport == "ble":
            assert isinstance(target, GoveeBleDevice)
            await self.ble_lights.power(target, on)
            return GoveeControlResult("Bluetooth", target.display_name)

        assert isinstance(target, GoveeLanDevice)
        await self.lan.power(target, on)
        return GoveeControlResult("WLAN/LAN", target.display_name)

    async def brightness_device(
        self,
        selector: str,
        value: int,
    ) -> GoveeControlResult:
        transport, target = await self._resolve_control_target(selector)
        if transport == "ble":
            assert isinstance(target, GoveeBleDevice)
            await self.ble_lights.brightness(target, value)
            return GoveeControlResult("Bluetooth", target.display_name)

        assert isinstance(target, GoveeLanDevice)
        await self.lan.brightness(target, value)
        return GoveeControlResult("WLAN/LAN", target.display_name)

    async def color_device(
        self,
        selector: str,
        r: int,
        g: int,
        b: int,
    ) -> GoveeControlResult:
        transport, target = await self._resolve_control_target(selector)
        if transport == "ble":
            assert isinstance(target, GoveeBleDevice)
            await self.ble_lights.color(target, r, g, b)
            return GoveeControlResult("Bluetooth", target.display_name)

        assert isinstance(target, GoveeLanDevice)
        await self.lan.color(target, r, g, b)
        return GoveeControlResult("WLAN/LAN", target.display_name)

    async def apply_preset(
        self,
        selector: str,
        preset: str,
    ) -> GoveeControlResult:
        name = preset.strip().lower()

        if name == "on":
            return await self.power_device(selector, True)
        if name == "off":
            return await self.power_device(selector, False)

        if name == "night":
            result = await self.power_device(selector, True)
            await self.color_device(selector, 255, 147, 41)
            await self.brightness_device(selector, 12)
            return result

        if name == "gaming":
            result = await self.power_device(selector, True)
            await self.color_device(selector, 100, 30, 255)
            await self.brightness_device(selector, 45)
            return result

        raise ValueError(f"Unbekanntes Preset: {preset}")

    async def apply_preset_all(self, preset: str) -> GoveeBatchResult:
        if not self.controllable_devices():
            await self.discover_all()

        applied = 0
        errors: list[str] = []
        for device in self.controllable_devices():
            try:
                await self.apply_preset(device.selector, preset)
                applied += 1
            except Exception as exc:
                errors.append(
                    f"{device.model}/{device.transport}: {self._short_error(exc)}"
                )

        return GoveeBatchResult(
            applied=applied,
            failed=len(errors),
            errors=tuple(errors),
        )

    async def _resolve_control_target(
        self,
        selector: str,
    ) -> tuple[str, GoveeLanDevice | GoveeBleDevice]:
        raw = selector.strip()
        lowered = raw.lower()

        if lowered.startswith("lan:"):
            await self.ensure_lan_devices()
            return "lan", self.lan.resolve(raw[4:])

        if lowered.startswith("ble:"):
            await self.ensure_ble_devices()
            target = self.ble.resolve(raw[4:])
            if not self.ble_lights.supports(target):
                raise RuntimeError(
                    f"{target.model or target.name} wird erkannt, aber direkte "
                    "BLE-Lichtsteuerung ist für dieses Modell nicht freigeschaltet."
                )
            return "ble", target

        try:
            await self.ensure_lan_devices()
            return "lan", self.lan.resolve(raw)
        except LookupError:
            await self.ensure_ble_devices()
            target = self.ble.resolve(raw)
            if not self.ble_lights.supports(target):
                raise RuntimeError(
                    f"{target.model or target.name} wird erkannt, aber direkte "
                    "BLE-Lichtsteuerung ist für dieses Modell nicht freigeschaltet."
                )
            return "ble", target

    @staticmethod
    def _short_error(exc: Exception) -> str:
        if isinstance(exc, GoveeBleUnavailableError):
            return str(exc)
        if isinstance(exc, GoveeBleScanError):
            return str(exc)
        message = str(exc).strip()
        return message or type(exc).__name__
