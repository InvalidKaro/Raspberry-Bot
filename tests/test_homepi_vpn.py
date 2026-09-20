from __future__ import annotations

import asyncio
from pathlib import Path

from services import homepi_vpn


def test_profiles_only_safe_regular_files(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "japan.conf").write_text("[Interface]\n")
    (tmp_path / "us-east.conf").write_text("[Interface]\n")
    (tmp_path / "bad name.conf").write_text("")
    (tmp_path / "not-a-profile.txt").write_text("")
    (tmp_path / "link.conf").symlink_to(tmp_path / "japan.conf")
    monkeypatch.setenv("HOMEPI_VPN_PROFILE_DIR", str(tmp_path))
    assert homepi_vpn.profiles() == ["japan", "us-east"]


def test_status_does_not_expose_keys_or_modify_network(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "japan.conf").write_text("PrivateKey = SECRET-DO-NOT-EXPOSE\n")
    monkeypatch.setenv("HOMEPI_VPN_PROFILE_DIR", str(tmp_path))

    async def fake_run(*args: str) -> tuple[int, str]:
        assert args == ("wg", "show", "interfaces")
        return 0, "japan tailscale0"

    monkeypatch.setattr(homepi_vpn, "_run", fake_run)
    data = asyncio.run(homepi_vpn.status())
    assert data["active_profiles"] == ["japan"]
    assert data["mode"] == "read-only"
    assert "SECRET-DO-NOT-EXPOSE" not in str(data)
