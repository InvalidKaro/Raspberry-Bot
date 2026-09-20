from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from services import homepi_vpn_proxy as vpn


KEY = "A" * 43 + "="
PROFILE = f"""[Interface]
Address = 10.0.0.2/32
PrivateKey = {KEY}
DNS = 1.1.1.1

[Peer]
PublicKey = {KEY}
AllowedIPs = 0.0.0.0/0, ::/0
Endpoint = vpn.example.org:51820
PersistentKeepalive = 25
"""


@pytest.fixture
def directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "profiles"
    root.mkdir(mode=0o700)
    monkeypatch.setenv("HOMEPI_VPN_PROFILE_DIR", str(root))
    monkeypatch.setenv("HOMEPI_VPN_RUNTIME_DIR", str(tmp_path / "runtime"))
    return root


def add_profile(directory: Path, name: str, content: str = PROFILE) -> Path:
    path = directory / (name + ".conf")
    path.write_text(content, encoding="utf-8")
    path.chmod(0o600)
    return path


def test_restricts_profile_inventory_and_labels(directory: Path) -> None:
    add_profile(directory, "de-frankfurt")
    add_profile(directory, "nl-amsterdam")
    (directory / "bad name.conf").write_text(PROFILE)
    (directory / "linked.conf").symlink_to(directory / "de-frankfurt.conf")
    (directory.parent / "locations.json").write_text(
        '{"de-frankfurt": {"country":"DE", "city":"Frankfurt"}}', encoding="utf-8"
    )
    assert vpn.list_profiles() == ["de-frankfurt", "nl-amsterdam"]
    locations = vpn.locations()
    assert locations[0] == {"profile": "de-frankfurt", "country": "DE", "city": "Frankfurt", "verified": False}


def test_rejects_unsafe_keys_and_dangerous_hooks(directory: Path) -> None:
    add_profile(directory, "safe")
    result = vpn._safe_wireproxy_config("safe")
    assert result.endswith("[Socks5]\nBindAddress = 127.0.0.1:25344\n")
    assert result.count("[Socks5]") == 1
    with pytest.raises(vpn.VPNError):
        vpn._safe_wireproxy_config("../safe")
    config_path = directory / "safe.conf"
    config_path.chmod(0o644)
    with pytest.raises(vpn.VPNError, match="chmod 600"):
        vpn._safe_wireproxy_config("safe")
    config_path.chmod(0o600)
    add_profile(directory, "bad", PROFILE.replace("Address = 10.0.0.2/32", "PostUp = ip route replace default"))
    with pytest.raises(vpn.VPNError, match="Unsupported"):
        vpn._safe_wireproxy_config("bad")
    add_profile(directory, "bind", PROFILE + "\n[Socks5]\nBindAddress=0.0.0.0:25344\n")
    with pytest.raises(vpn.VPNError, match="Only"):
        vpn._safe_wireproxy_config("bind")


def test_no_process_is_started_for_invalid_profile(directory: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    add_profile(directory, "bad", PROFILE.replace("AllowedIPs = 0.0.0.0/0, ::/0", "AllowedIPs = 10.0.0.0/8"))
    monkeypatch.setattr(vpn.shutil, "which", lambda _binary: "/bin/false")

    async def never_spawn(*args: str, **kwargs: object) -> None:
        raise AssertionError("A rejected profile must never spawn a process")

    monkeypatch.setattr(vpn.asyncio, "create_subprocess_exec", never_spawn)
    controller = vpn.ProxyController(tmp_path)
    with pytest.raises(vpn.VPNError):
        asyncio.run(controller.connect("bad"))


def test_connect_switch_disconnect_only_manages_wireproxy(
    directory: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    add_profile(directory, "de")
    add_profile(directory, "nl")
    monkeypatch.setattr(vpn.shutil, "which", lambda _binary: "/usr/local/bin/wireproxy")
    commands: list[tuple[str, ...]] = []

    class FakeProcess:
        def __init__(self, returncode: int | None):
            self.returncode = returncode

        def terminate(self) -> None:
            self.returncode = -15

        def kill(self) -> None:
            self.returncode = -9

        async def wait(self) -> int:
            return self.returncode or 0

    async def spawn(*args: str, **kwargs: object) -> FakeProcess:
        commands.append(args)
        return FakeProcess(0 if "-n" in args else None)

    controller = vpn.ProxyController(tmp_path)

    async def ready() -> bool:
        return controller.proc is not None and controller.proc.returncode is None

    monkeypatch.setattr(vpn.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(vpn, "_port_ready", ready)

    async def scenario() -> None:
        first = await controller.connect("de")
        assert first["active_profile"] == "de"
        assert first["proxy_ready"]
        second = await controller.connect("nl")
        assert second["active_profile"] == "nl"
        assert controller.config_path is not None and controller.config_path.exists()
        await controller.disconnect()
        assert controller.proc is None
        assert controller.config_path is None
        assert not list((tmp_path / "runtime").glob("proxy-*.conf"))

    asyncio.run(scenario())
    assert all(command[0] == "/usr/local/bin/wireproxy" for command in commands)
    assert [command[1] for command in commands] == ["-n", "-c", "-n", "-c"]
