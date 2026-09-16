from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import re
import socket
import urllib.parse
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping

import aiohttp

logger = logging.getLogger(__name__)


class ControlState(StrEnum):
    CONTROLLABLE = "controllable"
    AUTH_REQUIRED = "auth_required"
    PAIRING_REQUIRED = "pairing_required"
    DETECTED_ONLY = "detected_only"


@dataclass(frozen=True, slots=True)
class UniversalDevice:
    selector: str
    name: str
    vendor: str
    model: str
    protocol: str
    transport: str
    address: str
    state: ControlState
    capabilities: tuple[str, ...]
    reason: str = ""

    @property
    def controllable(self) -> bool:
        return self.state == ControlState.CONTROLLABLE and bool(self.capabilities)

    @property
    def display_name(self) -> str:
        vendor = self.vendor or "Unknown"
        model = self.model or self.name or "Device"
        return f"{vendor} {model}".strip()


@dataclass(frozen=True, slots=True)
class ScanResult:
    devices: tuple[UniversalDevice, ...]
    unnamed_ble_count: int = 0
    scanned_hosts: int = 0

    @property
    def controllable(self) -> tuple[UniversalDevice, ...]:
        return tuple(device for device in self.devices if device.controllable)


class UniversalDeviceError(RuntimeError):
    pass


class UniversalDeviceService:
    """Vendor-neutral local device discovery and safe control router.

    Discovery is capability based. Unknown devices are never written to. A device
    becomes controllable only when a supported local protocol is positively
    identified and the adapter can derive a safe command surface.
    """

    _MDNS_TYPES = (
        "_http._tcp.local.",
        "_wled._tcp.local.",
        "_shelly._tcp.local.",
        "_hap._tcp.local.",
        "_matter._tcp.local.",
        "_matterc._udp.local.",
        "_esphomelib._tcp.local.",
    )

    def __init__(self, *, govee_service: Any | None = None) -> None:
        self.govee_service = govee_service
        self.devices: dict[str, UniversalDevice] = {}
        self._scan_lock = asyncio.Lock()
        self._http_limit = asyncio.Semaphore(40)
        self._host_limit = asyncio.Semaphore(32)
        self._ble_radio_lock = getattr(govee_service, "_ble_radio_lock", None) or asyncio.Lock()

    async def scan(self, *, deep: bool = False, include_ble: bool = True) -> ScanResult:
        async with self._scan_lock:
            registry: dict[str, UniversalDevice] = {}
            unnamed_ble = 0

            if self.govee_service is not None:
                try:
                    await self.govee_service.refresh_devices()
                except Exception:
                    logger.debug("Govee refresh failed during universal scan", exc_info=True)
                self._merge_govee(registry)

            host_candidates: set[str] = set()
            mdns_devices, mdns_hosts = await self._discover_mdns()
            for device in mdns_devices:
                registry.setdefault(device.selector, device)
            host_candidates.update(mdns_hosts)

            ssdp_devices, ssdp_hosts = await self._discover_ssdp()
            for device in ssdp_devices:
                registry.setdefault(device.selector, device)
            host_candidates.update(ssdp_hosts)

            host_candidates.update(await self._neighbor_hosts())
            if deep:
                host_candidates.update(await self._local_subnet_hosts())

            host_candidates = {
                host
                for host in host_candidates
                if self._valid_local_ipv4(host)
            }

            timeout = aiohttp.ClientTimeout(total=1.0, connect=0.35, sock_read=0.65)
            connector = aiohttp.TCPConnector(limit=48, ttl_dns_cache=30)
            async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
                results = await asyncio.gather(
                    *(self._probe_host(session, host) for host in sorted(host_candidates)),
                    return_exceptions=True,
                )
            for result in results:
                if isinstance(result, Exception):
                    logger.debug("LAN device probe failed", exc_info=result)
                    continue
                for device in result:
                    registry[device.selector] = device

            if include_ble:
                ble_devices, unnamed_ble = await self._discover_generic_ble()
                for device in ble_devices:
                    registry.setdefault(device.selector, device)

            self.devices = dict(sorted(registry.items(), key=lambda item: self._sort_key(item[1])))
            return ScanResult(
                devices=tuple(self.devices.values()),
                unnamed_ble_count=unnamed_ble,
                scanned_hosts=len(host_candidates),
            )

    def list_devices(self) -> list[UniversalDevice]:
        return list(self.devices.values())

    def controllable_devices(self) -> list[UniversalDevice]:
        return [device for device in self.devices.values() if device.controllable]

    def resolve(self, selector: str) -> UniversalDevice:
        raw = selector.strip()
        if raw in self.devices:
            return self.devices[raw]
        lowered = raw.lower()
        matches = [
            device
            for device in self.devices.values()
            if lowered in device.selector.lower()
            or lowered in device.name.lower()
            or lowered in device.display_name.lower()
            or lowered in device.address.lower()
        ]
        if not matches:
            raise LookupError("Gerät nicht gefunden. Führe zuerst `/devices scan` aus.")
        if len(matches) > 1:
            raise LookupError("Mehrere Geräte passen. Nutze die Autovervollständigung.")
        return matches[0]

    async def power(self, selector: str, on: bool) -> UniversalDevice:
        device = self._require_capability(selector, "power")
        if device.protocol == "Govee":
            assert self.govee_service is not None
            await self.govee_service.power_device(device.selector.removeprefix("govee:"), on)
        elif device.protocol == "WLED JSON":
            await self._wled_state(device.address, {"on": bool(on)})
        elif device.protocol == "Shelly RPC":
            await self._shelly_set(device, on=bool(on))
        elif device.protocol == "Tasmota HTTP":
            await self._tasmota_command(device.address, f"Power {'On' if on else 'Off'}")
        else:
            raise UniversalDeviceError(f"Kein Power-Treiber für {device.protocol}.")
        return device

    async def brightness(self, selector: str, value: int) -> UniversalDevice:
        device = self._require_capability(selector, "brightness")
        level = max(1, min(100, int(value)))
        if device.protocol == "Govee":
            assert self.govee_service is not None
            await self.govee_service.brightness_device(device.selector.removeprefix("govee:"), level)
        elif device.protocol == "WLED JSON":
            await self._wled_state(device.address, {"on": True, "bri": round(level * 255 / 100)})
        elif device.protocol == "Shelly RPC":
            await self._shelly_set(device, brightness=level)
        elif device.protocol == "Tasmota HTTP":
            await self._tasmota_command(device.address, f"Dimmer {level}")
        else:
            raise UniversalDeviceError(f"Kein Helligkeits-Treiber für {device.protocol}.")
        return device

    async def color(self, selector: str, r: int, g: int, b: int) -> UniversalDevice:
        device = self._require_capability(selector, "rgb")
        rgb = tuple(max(0, min(255, int(value))) for value in (r, g, b))
        if device.protocol == "Govee":
            assert self.govee_service is not None
            await self.govee_service.color_device(device.selector.removeprefix("govee:"), *rgb)
        elif device.protocol == "WLED JSON":
            await self._wled_state(device.address, {"on": True, "seg": [{"col": [list(rgb)]}]})
        elif device.protocol == "Shelly RPC":
            await self._shelly_set(device, rgb=rgb)
        elif device.protocol == "Tasmota HTTP":
            await self._tasmota_command(device.address, f"Color {rgb[0]:02X}{rgb[1]:02X}{rgb[2]:02X}")
        else:
            raise UniversalDeviceError(f"Kein RGB-Treiber für {device.protocol}.")
        return device

    def _require_capability(self, selector: str, capability: str) -> UniversalDevice:
        device = self.resolve(selector)
        if device.state != ControlState.CONTROLLABLE:
            raise UniversalDeviceError(device.reason or "Gerät ist nicht lokal steuerbar.")
        if capability not in device.capabilities:
            raise UniversalDeviceError(
                f"{device.display_name} unterstützt `{capability}` nicht über {device.protocol}."
            )
        return device

    def _merge_govee(self, registry: dict[str, UniversalDevice]) -> None:
        if self.govee_service is None:
            return
        for item in self.govee_service.controllable_devices():
            caps = tuple(str(cap) for cap in item.capabilities)
            device = UniversalDevice(
                selector=f"govee:{item.selector}",
                name=item.display_name,
                vendor="Govee",
                model=item.model,
                protocol="Govee",
                transport=item.transport,
                address=item.detail,
                state=ControlState.CONTROLLABLE,
                capabilities=caps,
                reason="Lokaler Govee-Treiber verfügbar.",
            )
            registry[device.selector] = device

        for sensor in self.govee_service.sensor_devices():
            selector = f"govee-sensor:{sensor.address}"
            registry[selector] = UniversalDevice(
                selector=selector,
                name=sensor.name,
                vendor="Govee",
                model=sensor.model or "BLE sensor",
                protocol="Govee BLE sensor",
                transport="Bluetooth",
                address=sensor.masked_address,
                state=ControlState.DETECTED_ONLY,
                capabilities=("temperature", "humidity", "battery"),
                reason="Sensor wird ausgelesen; keine schreibbare Steuerfunktion freigegeben.",
            )

    async def _probe_host(self, session: aiohttp.ClientSession, host: str) -> list[UniversalDevice]:
        async with self._host_limit:
            probes = await asyncio.gather(
                self._probe_wled(session, host),
                self._probe_shelly(session, host),
                self._probe_tasmota(session, host),
                return_exceptions=True,
            )
        devices: list[UniversalDevice] = []
        for result in probes:
            if isinstance(result, Exception) or result is None:
                continue
            if isinstance(result, list):
                devices.extend(result)
            else:
                devices.append(result)
        return devices

    async def _probe_wled(self, session: aiohttp.ClientSession, host: str) -> UniversalDevice | None:
        status, payload, _ = await self._get_json(session, f"http://{host}/json/info")
        if status != 200 or not isinstance(payload, dict):
            return None
        brand = str(payload.get("brand") or "")
        if brand.lower() != "wled" and not ("leds" in payload and "ver" in payload and "name" in payload):
            return None
        name = str(payload.get("name") or "WLED")
        model = str(payload.get("product") or payload.get("arch") or "WLED light")
        return UniversalDevice(
            selector=f"wled:{host}",
            name=name,
            vendor="WLED",
            model=model,
            protocol="WLED JSON",
            transport="Wi-Fi/LAN",
            address=host,
            state=ControlState.CONTROLLABLE,
            capabilities=("power", "brightness", "rgb", "effects"),
            reason="WLED JSON API lokal erreichbar.",
        )

    async def _probe_shelly(self, session: aiohttp.ClientSession, host: str) -> list[UniversalDevice] | None:
        status, info, _ = await self._get_json(session, f"http://{host}/shelly")
        if status != 200 or not isinstance(info, dict):
            return None
        identity = str(info.get("id") or "")
        if not identity.lower().startswith("shelly") and not {"model", "gen", "mac"}.issubset(info):
            return None
        model = str(info.get("model") or info.get("app") or "Shelly")
        generation = int(info.get("gen") or 1)
        if generation < 2:
            return [
                UniversalDevice(
                    selector=f"shelly:{host}",
                    name=identity or "Shelly",
                    vendor="Shelly",
                    model=model,
                    protocol="Shelly Gen1",
                    transport="Wi-Fi/LAN",
                    address=host,
                    state=ControlState.DETECTED_ONLY,
                    capabilities=(),
                    reason="Shelly Gen1 erkannt; dieser sichere Treiber unterstützt aktuell Gen2+.",
                )
            ]
        if bool(info.get("auth_en")):
            return [
                UniversalDevice(
                    selector=f"shelly:{host}",
                    name=identity or "Shelly",
                    vendor="Shelly",
                    model=model,
                    protocol="Shelly RPC",
                    transport="Wi-Fi/LAN",
                    address=host,
                    state=ControlState.AUTH_REQUIRED,
                    capabilities=(),
                    reason="Shelly erkannt, aber lokale RPC-Authentifizierung ist aktiviert.",
                )
            ]

        status_code, status_payload, _ = await self._get_json(
            session,
            f"http://{host}/rpc/Shelly.GetStatus",
        )
        if status_code != 200 or not isinstance(status_payload, dict):
            return [
                UniversalDevice(
                    selector=f"shelly:{host}",
                    name=identity or "Shelly",
                    vendor="Shelly",
                    model=model,
                    protocol="Shelly RPC",
                    transport="Wi-Fi/LAN",
                    address=host,
                    state=ControlState.DETECTED_ONLY,
                    capabilities=(),
                    reason="Shelly erkannt, Komponenten konnten aber nicht gelesen werden.",
                )
            ]

        devices: list[UniversalDevice] = []
        for component in sorted(status_payload):
            match = re.fullmatch(r"(switch|light|rgb):(\d+)", component, flags=re.I)
            if not match:
                continue
            kind = match.group(1).lower()
            capabilities: tuple[str, ...]
            if kind == "switch":
                capabilities = ("power",)
            elif kind == "light":
                capabilities = ("power", "brightness")
            else:
                capabilities = ("power", "brightness", "rgb")
            devices.append(
                UniversalDevice(
                    selector=f"shelly:{host}:{kind}:{match.group(2)}",
                    name=f"{identity or 'Shelly'} {component}",
                    vendor="Shelly",
                    model=model,
                    protocol="Shelly RPC",
                    transport="Wi-Fi/LAN",
                    address=host,
                    state=ControlState.CONTROLLABLE,
                    capabilities=capabilities,
                    reason=f"Shelly {component} per lokaler RPC erkannt.",
                )
            )
        if devices:
            return devices
        return [
            UniversalDevice(
                selector=f"shelly:{host}",
                name=identity or "Shelly",
                vendor="Shelly",
                model=model,
                protocol="Shelly RPC",
                transport="Wi-Fi/LAN",
                address=host,
                state=ControlState.DETECTED_ONLY,
                capabilities=(),
                reason="Shelly erreichbar, aber keine freigegebene Switch/Light/RGB-Komponente gefunden.",
            )
        ]

    async def _probe_tasmota(self, session: aiohttp.ClientSession, host: str) -> UniversalDevice | None:
        status, payload, headers = await self._get_json(
            session,
            f"http://{host}/cm?cmnd=Status%200",
        )
        server = headers.get("Server", "") if headers else ""
        if status in {401, 403} and "tasmota" in server.lower():
            return UniversalDevice(
                selector=f"tasmota:{host}",
                name="Tasmota",
                vendor="Tasmota",
                model="Protected device",
                protocol="Tasmota HTTP",
                transport="Wi-Fi/LAN",
                address=host,
                state=ControlState.AUTH_REQUIRED,
                capabilities=(),
                reason="Tasmota erkannt, Web-Authentifizierung erforderlich.",
            )
        if status != 200 or not isinstance(payload, dict):
            return None
        if "Status" not in payload and "StatusFWR" not in payload and "StatusSTS" not in payload:
            return None
        base = payload.get("Status") if isinstance(payload.get("Status"), dict) else {}
        status_sts = payload.get("StatusSTS") if isinstance(payload.get("StatusSTS"), dict) else {}
        model = str(base.get("Module") or "Tasmota")
        friendly = base.get("FriendlyName")
        if isinstance(friendly, list) and friendly:
            name = str(friendly[0])
        else:
            name = str(base.get("DeviceName") or "Tasmota")
        capabilities: list[str] = []
        if any(str(key).upper().startswith("POWER") for key in status_sts):
            capabilities.append("power")
        if "Dimmer" in status_sts:
            capabilities.append("brightness")
        if "Color" in status_sts:
            capabilities.append("rgb")
        state = ControlState.CONTROLLABLE if capabilities else ControlState.DETECTED_ONLY
        return UniversalDevice(
            selector=f"tasmota:{host}",
            name=name,
            vendor="Tasmota",
            model=model,
            protocol="Tasmota HTTP",
            transport="Wi-Fi/LAN",
            address=host,
            state=state,
            capabilities=tuple(capabilities),
            reason=(
                "Tasmota HTTP command API lokal erreichbar."
                if capabilities
                else "Tasmota erkannt, aber keine unterstützte schreibbare Capability gemeldet."
            ),
        )

    async def _discover_generic_ble(self) -> tuple[list[UniversalDevice], int]:
        try:
            from bleak import BleakScanner
        except ImportError:
            return [], 0
        try:
            async with self._ble_radio_lock:
                discovered = await BleakScanner.discover(timeout=5.0, return_adv=True)
        except Exception:
            logger.debug("Generic BLE discovery failed", exc_info=True)
            return [], 0

        devices: list[UniversalDevice] = []
        unnamed = 0
        for address, pair in discovered.items():
            device, advertisement = pair
            name = str(
                getattr(advertisement, "local_name", None)
                or getattr(device, "name", None)
                or ""
            ).strip()
            if not name:
                unnamed += 1
                continue
            lowered = name.lower()
            if "govee" in lowered or lowered.startswith("gvh"):
                continue
            service_uuids = tuple(getattr(advertisement, "service_uuids", ()) or ())
            reason = "Bluetooth-Gerät erkannt; kein sicherer herstellerunabhängiger Schreibtreiber identifiziert."
            protocol = "BLE advertisement"
            state = ControlState.DETECTED_ONLY
            if any("0000feaf" in uuid.lower() for uuid in service_uuids):
                protocol = "Matter BLE commissioning"
                state = ControlState.PAIRING_REQUIRED
                reason = "Matter-Commissioning erkannt; benötigt einen Matter-Controller/Pairing-Flow."
            masked = self._mask_mac(address)
            selector = f"ble-observed:{address}"
            devices.append(
                UniversalDevice(
                    selector=selector,
                    name=name,
                    vendor="Unknown",
                    model=name,
                    protocol=protocol,
                    transport="Bluetooth",
                    address=masked,
                    state=state,
                    capabilities=(),
                    reason=reason,
                )
            )
        return devices, unnamed

    async def _discover_mdns(self) -> tuple[list[UniversalDevice], set[str]]:
        try:
            from zeroconf import ServiceStateChange
            from zeroconf.asyncio import AsyncServiceBrowser, AsyncZeroconf
        except ImportError:
            return [], set()

        hosts: set[str] = set()
        observed: dict[tuple[str, str], str] = {}
        aiozc = AsyncZeroconf()

        async def resolve(service_type: str, name: str) -> None:
            try:
                info = await aiozc.async_get_service_info(service_type, name, timeout=800)
            except Exception:
                return
            if info is None:
                return
            for address in info.parsed_addresses():
                if self._valid_local_ipv4(address):
                    hosts.add(address)
                    observed[(service_type, address)] = name

        tasks: set[asyncio.Task[None]] = set()

        def handler(_zc: Any, service_type: str, name: str, state_change: Any) -> None:
            if state_change not in {ServiceStateChange.Added, ServiceStateChange.Updated}:
                return
            task = asyncio.create_task(resolve(service_type, name))
            tasks.add(task)
            task.add_done_callback(tasks.discard)

        browser = AsyncServiceBrowser(aiozc.zeroconf, list(self._MDNS_TYPES), handlers=[handler])
        try:
            await asyncio.sleep(1.8)
            if tasks:
                await asyncio.gather(*tuple(tasks), return_exceptions=True)
        finally:
            await browser.async_cancel()
            await aiozc.async_close()

        devices: list[UniversalDevice] = []
        for (service_type, host), name in observed.items():
            protocol = None
            state = ControlState.DETECTED_ONLY
            reason = "mDNS-Dienst erkannt; Protokoll wird auf lokale Steuerbarkeit geprüft."
            vendor = "Unknown"
            if service_type == "_hap._tcp.local.":
                protocol = "HomeKit Accessory Protocol"
                state = ControlState.PAIRING_REQUIRED
                reason = "HomeKit-Gerät erkannt; benötigt Pairing-Schlüssel/Controller-Session."
            elif service_type in {"_matter._tcp.local.", "_matterc._udp.local."}:
                protocol = "Matter"
                state = ControlState.PAIRING_REQUIRED
                reason = "Matter-Gerät erkannt; benötigt Commissioning in einen Matter-Controller."
            elif service_type == "_esphomelib._tcp.local.":
                protocol = "ESPHome Native API"
                reason = "ESPHome erkannt; native API benötigt einen dedizierten Adapter und ggf. Encryption Key."
            if protocol is None:
                continue
            selector = f"observed:{urllib.parse.quote(protocol, safe='')}:{host}"
            devices.append(
                UniversalDevice(
                    selector=selector,
                    name=name.removesuffix(f".{service_type}"),
                    vendor=vendor,
                    model="mDNS device",
                    protocol=protocol,
                    transport="Wi-Fi/LAN",
                    address=host,
                    state=state,
                    capabilities=(),
                    reason=reason,
                )
            )
        return devices, hosts

    async def _discover_ssdp(self) -> tuple[list[UniversalDevice], set[str]]:
        message = (
            "M-SEARCH * HTTP/1.1\r\n"
            "HOST: 239.255.255.250:1900\r\n"
            'MAN: "ssdp:discover"\r\n'
            "MX: 1\r\n"
            "ST: ssdp:all\r\n\r\n"
        ).encode("ascii")

        def perform() -> list[str]:
            locations: list[str] = []
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            try:
                sock.settimeout(1.2)
                sock.sendto(message, ("239.255.255.250", 1900))
                while True:
                    try:
                        data, _ = sock.recvfrom(65535)
                    except socket.timeout:
                        break
                    text = data.decode("utf-8", errors="ignore")
                    for line in text.splitlines():
                        if line.lower().startswith("location:"):
                            locations.append(line.split(":", 1)[1].strip())
            finally:
                sock.close()
            return locations

        try:
            locations = await asyncio.to_thread(perform)
        except OSError:
            return [], set()

        hosts: set[str] = set()
        for location in locations:
            try:
                parsed = urllib.parse.urlparse(location)
                host = parsed.hostname
            except ValueError:
                continue
            if host and self._valid_local_ipv4(host):
                hosts.add(host)
        return [], hosts

    async def _neighbor_hosts(self) -> set[str]:
        try:
            proc = await asyncio.create_subprocess_exec(
                "ip",
                "-j",
                "neigh",
                "show",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=2.0)
            rows = json.loads(stdout.decode("utf-8"))
        except Exception:
            return set()
        return {
            str(row.get("dst"))
            for row in rows
            if isinstance(row, dict) and self._valid_local_ipv4(str(row.get("dst") or ""))
        }

    async def _local_subnet_hosts(self) -> set[str]:
        try:
            proc = await asyncio.create_subprocess_exec(
                "ip",
                "-j",
                "route",
                "get",
                "1.1.1.1",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=2.0)
            routes = json.loads(stdout.decode("utf-8"))
            source = str(routes[0].get("prefsrc") or routes[0].get("src") or "")
            dev = str(routes[0].get("dev") or "")
            if not source or not dev:
                return set()
            proc = await asyncio.create_subprocess_exec(
                "ip",
                "-j",
                "addr",
                "show",
                "dev",
                dev,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=2.0)
            interfaces = json.loads(stdout.decode("utf-8"))
            prefix = 24
            for info in interfaces[0].get("addr_info", []):
                if info.get("family") == "inet" and info.get("local") == source:
                    prefix = int(info.get("prefixlen") or 24)
                    break
            network = ipaddress.ip_network(f"{source}/{max(prefix, 24)}", strict=False)
            return {str(host) for host in network.hosts() if str(host) != source}
        except Exception:
            return set()

    async def _get_json(
        self,
        session: aiohttp.ClientSession,
        url: str,
    ) -> tuple[int, Any | None, Mapping[str, str]]:
        try:
            async with self._http_limit:
                async with session.get(url, allow_redirects=False) as response:
                    headers = response.headers
                    if response.status != 200:
                        return response.status, None, headers
                    raw = await response.read()
            if len(raw) > 1_000_000:
                return 200, None, headers
            return 200, json.loads(raw.decode("utf-8", errors="strict")), headers
        except (aiohttp.ClientError, asyncio.TimeoutError, UnicodeDecodeError, json.JSONDecodeError):
            return 0, None, {}

    async def _wled_state(self, host: str, payload: dict[str, Any]) -> None:
        timeout = aiohttp.ClientTimeout(total=2.5)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(f"http://{host}/json/state", json=payload) as response:
                if response.status >= 400:
                    raise UniversalDeviceError(f"WLED antwortete mit HTTP {response.status}.")
                await response.read()

    async def _shelly_set(
        self,
        device: UniversalDevice,
        *,
        on: bool | None = None,
        brightness: int | None = None,
        rgb: tuple[int, int, int] | None = None,
    ) -> None:
        parts = device.selector.split(":")
        if len(parts) != 4:
            raise UniversalDeviceError("Shelly-Komponente konnte nicht aufgelöst werden.")
        _, host, kind, component_id = parts
        component = kind.capitalize()
        params: dict[str, Any] = {"id": int(component_id)}
        if on is not None:
            params["on"] = on
        if brightness is not None:
            params["brightness"] = max(0, min(100, int(brightness)))
        if rgb is not None:
            params["rgb"] = list(rgb)
        if kind == "switch" and (brightness is not None or rgb is not None):
            raise UniversalDeviceError("Shelly Switch unterstützt hier nur Power.")
        if kind == "light" and rgb is not None:
            raise UniversalDeviceError("Shelly Light unterstützt hier kein RGB.")
        timeout = aiohttp.ClientTimeout(total=2.5)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                f"http://{host}/rpc/{component}.Set",
                json=params,
            ) as response:
                if response.status >= 400:
                    raise UniversalDeviceError(f"Shelly antwortete mit HTTP {response.status}.")
                payload = await response.json(content_type=None)
                if isinstance(payload, dict) and "code" in payload and "message" in payload:
                    raise UniversalDeviceError(str(payload.get("message")))

    async def _tasmota_command(self, host: str, command: str) -> None:
        encoded = urllib.parse.quote(command, safe="")
        timeout = aiohttp.ClientTimeout(total=2.5)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(f"http://{host}/cm?cmnd={encoded}") as response:
                if response.status >= 400:
                    raise UniversalDeviceError(f"Tasmota antwortete mit HTTP {response.status}.")
                await response.read()

    @staticmethod
    def _valid_local_ipv4(value: str) -> bool:
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            return False
        return bool(address.version == 4 and (address.is_private or address.is_link_local))

    @staticmethod
    def _mask_mac(address: str) -> str:
        parts = address.split(":")
        if len(parts) >= 2:
            return f"…:{parts[-2]}:{parts[-1]}"
        return "hidden"

    @staticmethod
    def _sort_key(device: UniversalDevice) -> tuple[int, str, str]:
        rank = {
            ControlState.CONTROLLABLE: 0,
            ControlState.AUTH_REQUIRED: 1,
            ControlState.PAIRING_REQUIRED: 2,
            ControlState.DETECTED_ONLY: 3,
        }[device.state]
        return rank, device.vendor.lower(), device.display_name.lower()
