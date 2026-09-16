from __future__ import annotations

import pathlib
import sys
from datetime import UTC, datetime, timedelta

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.govee_ble import GoveeBleDevice, GoveeBleScanner
from services.govee_ble_light import GoveeBleLightController
from services.govee_climate_history import ClimateSample, GoveeClimateHistory
from services.govee_smart_home import GoveeSmartHomeService


def assert_packet(packet: bytes) -> None:
    assert len(packet) == 20
    checksum = 0
    for byte in packet:
        checksum ^= byte
    assert checksum == 0


def main() -> None:
    assert GoveeBleScanner.infer_model("Govee_H6076_6B39") == "H6076"
    assert GoveeBleScanner.infer_model("Govee_H6095_5ACD") == "H6095"
    assert GoveeBleScanner.infer_model("Govee_H617E_1A37") == "H617E"
    assert GoveeBleScanner.infer_model("GVH5075_0C70") == "H5075"
    assert GoveeBleScanner.infer_model("unknown") is None

    h617e = GoveeBleDevice(
        address="E1:E1:83:46:1A:37",
        name="Govee_H617E_1A37",
        rssi=-55,
        model="H617E",
    )
    h5075 = GoveeBleDevice(
        address="A4:C1:38:6B:0C:70",
        name="GVH5075_0C70",
        rssi=-63,
        model="H5075",
    )

    assert h617e.masked_address == "…:1A:37"
    assert GoveeBleLightController.supports(h617e)
    assert not GoveeBleLightController.supports(h5075)

    assert_packet(GoveeBleLightController._power_packet(True))
    assert_packet(GoveeBleLightController._power_packet(False))
    assert_packet(GoveeBleLightController._brightness_packet(45))
    assert_packet(GoveeBleLightController._color_packet(100, 30, 255))

    service = GoveeSmartHomeService()
    service.ble.devices[h617e.address] = h617e
    service.ble.devices[h5075.address] = h5075
    summaries = service.controllable_devices()
    assert len(summaries) == 1
    assert summaries[0].selector == f"ble:{h617e.address}"
    assert summaries[0].capabilities == ("power", "brightness", "rgb")

    service.ble.resolve(h617e.address)
    service.ble.resolve("H617E")

    now = datetime.now(UTC)
    samples = [
        ClimateSample(
            recorded_at=now - timedelta(minutes=10),
            temperature_c=21.0,
            humidity_percent=45.0,
            battery_percent=90.0,
        ),
        ClimateSample(
            recorded_at=now - timedelta(minutes=5),
            temperature_c=22.0,
            humidity_percent=47.0,
            battery_percent=90.0,
        ),
        ClimateSample(
            recorded_at=now,
            temperature_c=23.0,
            humidity_percent=49.0,
            battery_percent=89.0,
        ),
    ]
    stats = GoveeClimateHistory.summarize(samples)
    assert stats["temperature"].minimum == 21.0
    assert stats["temperature"].average == 22.0
    assert stats["temperature"].maximum == 23.0
    assert stats["humidity"].minimum == 45.0
    assert stats["humidity"].average == 47.0
    assert stats["humidity"].maximum == 49.0

    print("Smart-home smoke tests passed.")


if __name__ == "__main__":
    main()
