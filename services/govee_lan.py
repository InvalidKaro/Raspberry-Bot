from __future__ import annotations

import asyncio
import json
import logging
import socket
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class GoveeLanDevice:
    device_id: str
    sku: str
    ip: str
    last_seen: float
    raw: dict[str, Any]

    @property
    def display_name(self) -> str:
        return f"{self.sku or 'Govee'} · {self.ip}"


class _DiscoveryProtocol(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self.devices: dict[str, GoveeLanDevice] = {}

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        try:
            payload = json.loads(data.decode("utf-8", errors="strict"))
            msg = payload.get("msg", {})
            if msg.get("cmd") != "scan":
                return
            body = msg.get("data", {})
            ip = str(body.get("ip") or addr[0]).strip()
            device_id = str(body.get("device") or body.get("deviceId") or ip).strip()
            sku = str(body.get("sku") or body.get("model") or "Govee").strip()
            if not ip:
                return
            self.devices[device_id] = GoveeLanDevice(
                device_id=device_id,
                sku=sku,
                ip=ip,
                last_seen=time.monotonic(),
                raw=dict(body),
            )
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            logger.debug("Ignoring malformed Govee LAN discovery packet from %s", addr, exc_info=True)


class GoveeLanClient:
    MULTICAST_GROUP = "239.255.255.250"
    DISCOVERY_PORT = 4001
    LISTEN_PORT = 4002
    CONTROL_PORT = 4003

    def __init__(self) -> None:
        self.devices: dict[str, GoveeLanDevice] = {}
        self._command_lock = asyncio.Lock()

    @staticmethod
    def _packet(command: str, data: dict[str, Any]) -> bytes:
        return json.dumps(
            {"msg": {"cmd": command, "data": data}},
            separators=(",", ":"),
        ).encode("utf-8")

    async def discover(self, timeout: float = 2.5) -> list[GoveeLanDevice]:
        loop = asyncio.get_running_loop()
        transport: asyncio.DatagramTransport | None = None
        protocol = _DiscoveryProtocol()

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setblocking(False)

        try:
            sock.bind(("0.0.0.0", self.LISTEN_PORT))
            transport, _ = await loop.create_datagram_endpoint(
                lambda: protocol,
                sock=sock,
            )
            packet = self._packet("scan", {"account_topic": "reserve"})
            transport.sendto(packet, (self.MULTICAST_GROUP, self.DISCOVERY_PORT))
            await asyncio.sleep(max(0.5, min(timeout, 10.0)))
        except OSError as exc:
            raise RuntimeError(
                "Govee-LAN-Discovery konnte UDP-Port 4002 nicht verwenden. "
                "Prüfe, ob bereits ein anderer Govee-Dienst läuft."
            ) from exc
        finally:
            if transport is not None:
                transport.close()
            else:
                sock.close()

        self.devices.update(protocol.devices)
        return sorted(
            protocol.devices.values(),
            key=lambda item: (item.sku, item.ip, item.device_id),
        )

    def cached_devices(self) -> list[GoveeLanDevice]:
        return sorted(
            self.devices.values(),
            key=lambda item: (item.sku, item.ip, item.device_id),
        )

    def resolve(self, selector: str) -> GoveeLanDevice:
        value = selector.strip().lower()
        if not value:
            raise LookupError("Kein Gerät angegeben.")

        exact: list[GoveeLanDevice] = []
        partial: list[GoveeLanDevice] = []

        for device in self.devices.values():
            fields = (device.device_id, device.ip, device.sku)
            lowered = tuple(field.lower() for field in fields)
            if value in lowered:
                exact.append(device)
            elif any(value in field for field in lowered):
                partial.append(device)

        matches = exact or partial
        if not matches:
            raise LookupError(
                "Gerät nicht gefunden. Führe zuerst `/home scan` aus."
            )
        if len(matches) > 1:
            raise LookupError(
                "Mehrere Geräte passen. Wähle das Gerät aus der Autovervollständigung."
            )
        return matches[0]

    async def _send(self, device: GoveeLanDevice, command: str, data: dict[str, Any]) -> None:
        packet = self._packet(command, data)
        loop = asyncio.get_running_loop()

        async with self._command_lock:
            transport, _ = await loop.create_datagram_endpoint(
                asyncio.DatagramProtocol,
                local_addr=("0.0.0.0", 0),
            )
            try:
                transport.sendto(packet, (device.ip, self.CONTROL_PORT))
                await asyncio.sleep(0)
            finally:
                transport.close()

    async def power(self, device: GoveeLanDevice, on: bool) -> None:
        await self._send(device, "turn", {"value": 1 if on else 0})

    async def brightness(self, device: GoveeLanDevice, value: int) -> None:
        level = max(1, min(100, int(value)))
        await self._send(device, "brightness", {"value": level})

    async def color(self, device: GoveeLanDevice, r: int, g: int, b: int) -> None:
        rgb = {
            "r": max(0, min(255, int(r))),
            "g": max(0, min(255, int(g))),
            "b": max(0, min(255, int(b))),
        }
        await self._send(
            device,
            "colorwc",
            {"color": rgb, "colorTemInKelvin": 0},
        )

    async def color_temperature(self, device: GoveeLanDevice, kelvin: int) -> None:
        value = max(2000, min(9000, int(kelvin)))
        await self._send(
            device,
            "colorwc",
            {"color": {"r": 0, "g": 0, "b": 0}, "colorTemInKelvin": value},
        )
