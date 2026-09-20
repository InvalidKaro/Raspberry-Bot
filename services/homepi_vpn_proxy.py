"""Opt-in, application-scoped WireGuard SOCKS5 proxy. Never changes host routing/DNS."""
from __future__ import annotations

import asyncio
import configparser
import ipaddress
import json
import os
import re
import secrets
import shutil
import socket
import stat
from pathlib import Path

NAME = re.compile(r"^[a-z][a-z0-9_-]{0,30}$")
INTERFACE_KEYS = {"address", "privatekey", "dns", "mtu"}
PEER_KEYS = {"publickey", "presharedkey", "allowedips", "endpoint", "persistentkeepalive"}
PROXY_HOST = "127.0.0.1"
PROXY_PORT = 25344


class VPNError(ValueError):
    """An expected failure that can safely be shown without leaking credentials."""


def profile_dir() -> Path:
    return Path(os.getenv("HOMEPI_VPN_PROFILE_DIR", "~/.config/homepi-vpn/profiles")).expanduser()


def list_profiles() -> list[str]:
    root = profile_dir()
    if root.is_symlink() or not root.is_dir():
        return []
    names: list[str] = []
    for entry in root.iterdir():
        if entry.suffix == ".conf" and NAME.fullmatch(entry.stem) and not entry.is_symlink() and entry.is_file():
            names.append(entry.stem)
    return sorted(names)[:100]


def locations() -> list[dict[str, str | bool]]:
    """User-supplied labels, never claims of verified endpoint geography."""
    metadata: dict = {}
    file = profile_dir().parent / "locations.json"
    try:
        if not file.is_symlink() and file.is_file() and file.stat().st_size <= 8192:
            data = json.loads(file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                metadata = data
    except (OSError, UnicodeError, ValueError):
        pass
    result = []
    for name in list_profiles():
        details = metadata.get(name, {})
        if not isinstance(details, dict):
            details = {}
        country = details.get("country", "")
        city = details.get("city", "")
        if not isinstance(country, str) or not re.fullmatch(r"[A-Z]{2}", country):
            country = ""
        if not isinstance(city, str) or len(city) > 40 or not re.fullmatch(r"[\w .-]*", city):
            city = ""
        result.append({"profile": name, "country": country, "city": city, "verified": False})
    return result


def _read_profile(name: str) -> str:
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise VPNError("Invalid profile name.")
    root = profile_dir()
    if root.is_symlink() or not root.is_dir():
        raise VPNError("VPN profile directory not found.")
    path = root / (name + ".conf")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError as exc:
        raise VPNError("Profile is not accessible.") from exc
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 16384:
            raise VPNError("Profile must be a regular file up to 16 KiB.")
        if metadata.st_uid != os.geteuid() or metadata.st_mode & 0o077:
            raise VPNError("Profile must belong to the dashboard user and have chmod 600.")
        with os.fdopen(fd, "r", encoding="utf-8") as stream:
            fd = -1
            content = stream.read(16385)
    except (UnicodeError, OSError) as exc:
        raise VPNError("Profile cannot be read.") from exc
    finally:
        if fd != -1:
            os.close(fd)
    if len(content) > 16384:
        raise VPNError("Profile is too large.")
    return content


def _safe_wireproxy_config(name: str) -> str:
    """Only forward known WireGuard fields. Disallow wg-quick hooks and proxy overrides."""
    content = _read_profile(name)
    parser = configparser.ConfigParser(interpolation=None, strict=True, inline_comment_prefixes=("#", ";"))
    try:
        parser.read_string(content)
        if set(parser.sections()) != {"Interface", "Peer"} or parser.defaults():
            raise VPNError("Only [Interface] and one [Peer] are allowed.")
        interface = parser["Interface"]
        peer = parser["Peer"]
        if set(interface) - {"__name__"} - INTERFACE_KEYS or set(peer) - {"__name__"} - PEER_KEYS:
            raise VPNError("Unsupported WireGuard option (hooks and custom proxy settings are forbidden).")
        for key in ("address", "privatekey", "dns"):
            if not interface.get(key):
                raise VPNError("Missing required WireGuard interface option.")
        for key in ("publickey", "allowedips", "endpoint"):
            if not peer.get(key):
                raise VPNError("Missing required WireGuard peer option.")
        for value in list(interface.values()) + list(peer.values()):
            if "\n" in value or "\r" in value or value.lstrip().startswith("$"):
                raise VPNError("Invalid or environment-referenced profile value.")
        for ip in interface["address"].split(","):
            ipaddress.ip_interface(ip.strip())
        for ip in interface["dns"].split(","):
            ipaddress.ip_address(ip.strip())
        allowed = [ipaddress.ip_network(ip.strip(), strict=False) for ip in peer["allowedips"].split(",")]
        if ipaddress.ip_network("0.0.0.0/0") not in allowed:
            raise VPNError("Profile must route IPv4 through the WireGuard peer.")
        endpoint = peer["endpoint"].strip()
        if not re.fullmatch(r"(?:\[[0-9a-fA-F:]+\]|[a-zA-Z0-9.-]+):[0-9]{1,5}", endpoint):
            raise VPNError("Invalid WireGuard endpoint.")
        port = int(endpoint.rsplit(":", 1)[-1])
        if not 1 <= port <= 65535:
            raise VPNError("Invalid WireGuard endpoint port.")
        if not re.fullmatch(r"[A-Za-z0-9+/]{43}=", interface["privatekey"].strip()):
            raise VPNError("Invalid private key format.")
        if not re.fullmatch(r"[A-Za-z0-9+/]{43}=", peer["publickey"].strip()):
            raise VPNError("Invalid public key format.")
        if "presharedkey" in peer and not re.fullmatch(r"[A-Za-z0-9+/]{43}=", peer["presharedkey"].strip()):
            raise VPNError("Invalid preshared key format.")
    except (configparser.Error, ValueError, KeyError) as exc:
        if isinstance(exc, VPNError):
            raise
        raise VPNError("Invalid WireGuard configuration.") from exc
    # No WGConfig include, no PostUp/PostDown, no externally supplied BindAddress.
    return content.rstrip() + "\n\n[Socks5]\nBindAddress = 127.0.0.1:25344\n"


def _runtime_dir(repo_path: Path) -> Path:
    root = Path(os.getenv("HOMEPI_VPN_RUNTIME_DIR", str(repo_path / "data" / "vpn-runtime"))).expanduser()
    if root.is_symlink():
        raise VPNError("Runtime directory cannot be a symlink.")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    details = root.stat()
    if details.st_uid != os.geteuid() or details.st_mode & 0o077:
        raise VPNError("Runtime directory must be owned by the dashboard user with chmod 700.")
    return root


async def _port_ready() -> bool:
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(PROXY_HOST, PROXY_PORT), timeout=0.35
        )
        writer.close()
        await writer.wait_closed()
        return True
    except (OSError, asyncio.TimeoutError):
        return False


class ProxyController:
    """One subprocess owned by the dashboard process, with serialized operations."""

    def __init__(self, repo_path: Path):
        self.root = Path(repo_path)
        self.proc: asyncio.subprocess.Process | None = None
        self.active: str | None = None
        self.lock = asyncio.Lock()
        self.config_path: Path | None = None

    async def status(self) -> dict:
        running = bool(self.proc and self.proc.returncode is None)
        return {
            "ok": True,
            "mode": "application-proxy",
            "profiles": list_profiles(),
            "locations": locations(),
            "active_profile": self.active if running else None,
            "proxy": f"socks5h://{PROXY_HOST}:{PROXY_PORT}" if running else None,
            "proxy_ready": running and await _port_ready(),
            "message": (
                "Nur ausdrücklich konfigurierte Anwendungen nutzen den SOCKS5-Proxy. "
                "Pi-hole, Tailscale, SSH und die Standardroute bleiben unverändert. "
                "Proxy-Bereitschaft beweist weder VPN-Handschlag noch Ausstiegsland."
            ),
        }

    async def _stop_locked(self) -> None:
        proc = self.proc
        self.proc = None
        self.active = None
        if proc:
            if proc.returncode is None:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=4)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
            else:
                await proc.wait()
        if self.config_path is not None:
            self.config_path.unlink(missing_ok=True)
            self.config_path = None

    async def disconnect(self) -> dict:
        async with self.lock:
            await self._stop_locked()
            return await self.status()

    async def connect(self, profile: str) -> dict:
        async with self.lock:
            if self.active == profile and self.proc and self.proc.returncode is None:
                return await self.status()
            config_text = _safe_wireproxy_config(profile)  # preflight before stopping old proxy
            binary = shutil.which("wireproxy")
            if not binary:
                raise VPNError("wireproxy is not installed; see docs/HOMEPI_VPN.md.")
            runtime = _runtime_dir(self.root)
            candidate = runtime / ("proxy-" + secrets.token_hex(8) + ".conf")
            if candidate.exists() or candidate.is_symlink():
                raise VPNError("A proxy candidate path already exists.")
            fd = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as output:
                    output.write(config_text)
                test = await asyncio.create_subprocess_exec(
                    binary, "-n", "-c", str(candidate),
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                )
                try:
                    await asyncio.wait_for(test.wait(), timeout=8)
                except asyncio.TimeoutError as exc:
                    test.kill()
                    await test.wait()
                    raise VPNError("Wireproxy configuration check timed out.") from exc
                if test.returncode != 0:
                    raise VPNError("Wireproxy rejected this profile.")
                await self._stop_locked()
                if await _port_ready():
                    raise VPNError("The local SOCKS port is in use by another process.")
                self.proc = await asyncio.create_subprocess_exec(
                    binary, "-c", str(candidate), stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL, start_new_session=True,
                )
                self.config_path = candidate
                self.active = profile
                for _ in range(30):
                    if self.proc.returncode is not None:
                        raise VPNError("Wireproxy exited before the local proxy became ready.")
                    if await _port_ready():
                        return await self.status()
                    await asyncio.sleep(0.1)
                raise VPNError("Local proxy did not become ready.")
            except BaseException:
                if self.config_path == candidate:
                    await self._stop_locked()
                else:
                    candidate.unlink(missing_ok=True)
                raise

    async def close(self) -> None:
        async with self.lock:
            await self._stop_locked()


def socket_path(repo_path: Path) -> Path:
    return _runtime_dir(repo_path) / "control.sock"


async def request_controller(repo_path: Path, action: str, profile: str | None = None) -> dict:
    """Bot -> authenticated per-user local Unix socket; not exposed over the network."""
    reader, writer = await asyncio.wait_for(
        asyncio.open_unix_connection(str(socket_path(repo_path))), timeout=2
    )
    try:
        writer.write(json.dumps({"action": action, "profile": profile}).encode() + b"\n")
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), timeout=12)
        if not line:
            raise VPNError("Dashboard VPN controller unavailable.")
        return json.loads(line)
    finally:
        writer.close()
        await writer.wait_closed()
