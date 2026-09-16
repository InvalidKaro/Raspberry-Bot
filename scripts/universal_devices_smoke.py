from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.universal_devices import ControlState, UniversalDevice, UniversalDeviceService


def main() -> None:
    service = UniversalDeviceService()

    wled = UniversalDevice(
        selector="wled:192.168.1.50",
        name="Desk LEDs",
        vendor="WLED",
        model="FOSS",
        protocol="WLED JSON",
        transport="Wi-Fi/LAN",
        address="192.168.1.50",
        state=ControlState.CONTROLLABLE,
        capabilities=("power", "brightness", "rgb", "effects"),
        reason="test",
    )
    blocked = UniversalDevice(
        selector="observed:hap:192.168.1.51",
        name="Accessory",
        vendor="Unknown",
        model="mDNS device",
        protocol="HomeKit Accessory Protocol",
        transport="Wi-Fi/LAN",
        address="192.168.1.51",
        state=ControlState.PAIRING_REQUIRED,
        capabilities=(),
        reason="Pairing required",
    )
    service.devices = {wled.selector: wled, blocked.selector: blocked}

    assert service.resolve(wled.selector) == wled
    assert service.resolve("Desk LEDs") == wled
    assert service.controllable_devices() == [wled]
    assert service._require_capability(wled.selector, "power") == wled
    assert service._valid_local_ipv4("192.168.1.50")
    assert service._valid_local_ipv4("10.0.0.1")
    assert not service._valid_local_ipv4("8.8.8.8")
    assert service._mask_mac("AA:BB:CC:DD:EE:FF") == "…:EE:FF"

    try:
        service._require_capability(blocked.selector, "power")
    except Exception as exc:
        assert "Pairing" in str(exc)
    else:
        raise AssertionError("Pairing-only device unexpectedly became controllable")

    print("Universal device smoke tests passed.")


if __name__ == "__main__":
    main()
