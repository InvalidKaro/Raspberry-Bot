from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="meshtastic-smoke-") as temp_name:
        state_path = Path(temp_name) / "meshtastic_state.json"
        os.environ["MESHTASTIC_STATE_PATH"] = str(state_path)
        os.environ["MESHTASTIC_MAX_MESSAGES"] = "5"

        from meshtastic_service.collector import Collector

        now = time.time()
        local_user = {"id": "!00000001", "longName": "HomePi", "shortName": "HPI", "hwModel": "HELTEC_V3"}
        peer_user = {"id": "!00000002", "longName": "Peer Node", "shortName": "PEER", "hwModel": "HELTEC_V3"}
        nodes = {
            "!00000001": {"num": 1, "user": local_user, "lastHeard": now},
            "!00000002": {
                "num": 2,
                "user": peer_user,
                "lastHeard": now - 30,
                "snr": 7.25,
                "hopsAway": 1,
                "deviceMetrics": {"batteryLevel": 87, "voltage": 4.1},
            },
        }
        lora = SimpleNamespace(region="EU_868", modem_preset="LONG_FAST", tx_enabled=True, hop_limit=3)
        primary = SimpleNamespace(role="PRIMARY", settings=SimpleNamespace(name="LongFast"))
        interface = SimpleNamespace(
            nodes=nodes,
            nodesByNum={1: nodes["!00000001"], 2: nodes["!00000002"]},
            myInfo=SimpleNamespace(my_node_num=1),
            metadata=SimpleNamespace(firmware_version="2.7.11"),
            localNode=SimpleNamespace(localConfig=SimpleNamespace(lora=lora), channels=[primary]),
            devPath="/dev/ttyUSB0",
        )

        collector = Collector()
        collector.interface = interface
        collector.state["connected"] = True
        collector.state["device"] = interface.devPath
        collector.on_receive({"from": 2, "fromId": "!00000002", "rxRssi": -91, "rxSnr": 7.25}, interface)
        collector.on_text(
            {"id": 1234, "from": 2, "fromId": "!00000002", "decoded": {"text": "Hallo HomePi"}},
            interface,
        )

        payload = json.loads(state_path.read_text(encoding="utf-8"))
        assert payload["connected"] is True, payload
        assert payload["device"] == "/dev/ttyUSB0", payload
        assert payload["nodes_total"] == 1, payload
        assert payload["nodes_active_10m"] == 1, payload
        assert payload["local"]["name"] == "HomePi", payload
        assert payload["radio"]["region"] == "EU_868", payload
        assert payload["radio"]["modem_preset"] == "LONG_FAST", payload
        assert payload["radio"]["primary_channel"] == "LongFast", payload
        assert payload["radio"]["tx_enabled"] is True, payload
        assert payload["rx_packets"] == 1, payload
        assert payload["last_rssi"] == -91.0, payload
        assert payload["last_snr"] == 7.25, payload
        assert payload["last_message"]["text"] == "Hallo HomePi", payload
        assert len(payload["messages"]) == 1, payload
        assert payload["nodes"][0]["name"] == "Peer Node", payload

        restored = Collector()
        assert restored.state["rx_packets"] == 1, restored.state
        assert restored.state["messages"][0]["text"] == "Hallo HomePi", restored.state
        assert restored.state["connected"] is False, restored.state

    print("Meshtastic collector smoke test passed")


if __name__ == "__main__":
    main()
