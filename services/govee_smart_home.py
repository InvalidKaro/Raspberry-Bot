from __future__ import annotations

import asyncio
from dataclasses import dataclass

from services.govee_ble import GoveeBleDevice, GoveeBleScanner
from services.govee_ble_light import GoveeBleLightController
from services.govee_lan import GoveeLanClient, GoveeLanDevice


@dataclass(slots=True)
class GoveeDiscovery:
    lan: list[GoveeLanDevice]
    ble: list[GoveeBleDevice]


class GoveeSmartHomeService:
    """Low-overhead Govee controller.

    No background polling is started. LAN discovery and BLE scans only run
    when a command explicitly requests them, keeping idle CPU/RAM usage low.
    """

    def __init__(self) -> None:
        self.lan = GoveeLanClient()
        self.ble = GoveeBleScanner()
        self.ble_lights = GoveeBleLightController()
        self._discover_lock = asyncio.Lock()

    async def discover_all(
        self,
        *,
        lan_timeout: float = 2.5,
        ble_timeout: float = 5.0,
    ) -> GoveeDiscovery:
        async with self._discover_lock:
            lan_result, ble_result = await asyncio.gather(
                self.lan.discover(lan_timeout),
                self.ble.scan(ble_timeout),
                return_exceptions=True,
            )

        lan = [] if isinstance(lan_result, Exception) else lan_result
        ble = [] if isinstance(ble_result, Exception) else ble_result

        if isinstance(lan_result, Exception) and isinstance(ble_result, Exception):
            raise RuntimeError(f"LAN: {lan_result}; BLE: {ble_result}")
        return GoveeDiscovery(lan=lan, ble=ble)

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

    async def supported_ble_lights(self) -> list[GoveeBleDevice]:
        devices = await self.ensure_ble_devices()
        return [device for device in devices if self.ble_lights.supports(device)]

    async def ble_power(self, selector: str, on: bool) -> GoveeBleDevice:
        await self.ensure_ble_devices()
        target = self.ble.resolve(selector)
        await self.ble_lights.power(target, on)
        return target

    async def ble_brightness(self, selector: str, value: int) -> GoveeBleDevice:
        await self.ensure_ble_devices()
        target = self.ble.resolve(selector)
        await self.ble_lights.brightness(target, value)
        return target

    async def ble_color(self, selector: str, r: int, g: int, b: int) -> GoveeBleDevice:
        await self.ensure_ble_devices()
        target = self.ble.resolve(selector)
        await self.ble_lights.color(target, r, g, b)
        return target

    async def all_power(self, on: bool) -> int:
        devices = await self.ensure_lan_devices()
        if not devices:
            return 0
        await asyncio.gather(*(self.lan.power(device, on) for device in devices))
        return len(devices)

    async def scene(self, name: str) -> int:
        devices = await self.ensure_lan_devices()
        if not devices:
            return 0

        if name == "off":
            await asyncio.gather(*(self.lan.power(device, False) for device in devices))
        elif name == "on":
            await asyncio.gather(*(self.lan.power(device, True) for device in devices))
        elif name == "night":
            for device in devices:
                await self.lan.power(device, True)
                await self.lan.color_temperature(device, 2700)
                await self.lan.brightness(device, 12)
        elif name == "gaming":
            for device in devices:
                await self.lan.power(device, True)
                await self.lan.color(device, 100, 30, 255)
                await self.lan.brightness(device, 45)
        else:
            raise ValueError(f"Unbekannte Szene: {name}")

        return len(devices)
