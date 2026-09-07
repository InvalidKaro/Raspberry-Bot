from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

from pubsub import pub

import meshtastic.serial_interface

REPO_ROOT = Path(os.getenv("BOT_REPO_PATH", "/home/stefano/services/Raspberry-Bot"))
STATE_PATH = Path(os.getenv("MESHTASTIC_STATE_PATH", str(REPO_ROOT / "data" / "meshtastic_state.json")))
DEVICE = os.getenv("MESHTASTIC_DEVICE", "").strip()
RETRY_SECONDS = max(5, int(os.getenv("MESHTASTIC_RETRY_SECONDS", "15")))

logging.basicConfig(
    level=os.getenv("MESHTASTIC_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s homepi-meshtastic: %(message)s",
)
log = logging.getLogger("homepi-meshtastic")


class Collector:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.interface = None
        self.state: dict[str, Any] = {
            "connected": False,
            "device": DEVICE or "auto",
            "connected_at": 0,
            "last_error": None,
            "nodes_total": 0,
            "nodes_active_10m": 0,
            "nodes_active_60m": 0,
            "rx_packets": 0,
            "last_packet_at": 0,
            "last_rssi": None,
            "last_snr": None,
            "last_from": "",
            "last_message": {"text": "", "from": "", "received_at": 0, "packet_id": None},
            "local": {"name": "", "id": "", "firmware": "", "hardware": ""},
            "updated_at": "",
        }

    def _refresh_nodes(self) -> None:
        iface = self.interface
        nodes = getattr(iface, "nodes", None) if iface is not None else None
        if not isinstance(nodes, dict):
            return

        now = time.time()
        active_10m = 0
        active_60m = 0
        for node in nodes.values():
            if not isinstance(node, dict):
                continue
            try:
                last_heard = float(node.get("lastHeard") or 0)
            except (TypeError, ValueError):
                last_heard = 0
            if last_heard and now - last_heard <= 600:
                active_10m += 1
            if last_heard and now - last_heard <= 3600:
                active_60m += 1

        self.state["nodes_total"] = len(nodes)
        self.state["nodes_active_10m"] = active_10m
        self.state["nodes_active_60m"] = active_60m

        my_num = getattr(getattr(iface, "myInfo", None), "my_node_num", None)
        if my_num is not None:
            for node in nodes.values():
                if not isinstance(node, dict) or node.get("num") != my_num:
                    continue
                user = node.get("user") if isinstance(node.get("user"), dict) else {}
                self.state["local"]["name"] = str(user.get("longName") or user.get("shortName") or "")
                self.state["local"]["id"] = str(user.get("id") or "")
                self.state["local"]["hardware"] = str(user.get("hwModel") or "")
                break

        metadata = getattr(iface, "metadata", None)
        firmware = getattr(metadata, "firmware_version", None)
        if firmware:
            self.state["local"]["firmware"] = str(firmware)

    def _write(self) -> None:
        with self.lock:
            self._refresh_nodes()
            self.state["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = STATE_PATH.with_suffix(STATE_PATH.suffix + ".tmp")
            tmp.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(STATE_PATH)

    def _sender_name(self, packet: dict[str, Any]) -> str:
        if packet.get("fromId"):
            return str(packet["fromId"])
        node_num = packet.get("from")
        iface = self.interface
        nodes = getattr(iface, "nodesByNum", None) if iface is not None else None
        if isinstance(nodes, dict) and node_num in nodes and isinstance(nodes[node_num], dict):
            user = nodes[node_num].get("user")
            if isinstance(user, dict):
                return str(user.get("longName") or user.get("shortName") or user.get("id") or node_num)
        if node_num is not None:
            try:
                return f"!{int(node_num):08x}"
            except (TypeError, ValueError):
                return str(node_num)
        return "Unbekannt"

    @staticmethod
    def _metric(packet: dict[str, Any], *names: str) -> float | None:
        for name in names:
            if packet.get(name) is None:
                continue
            try:
                return float(packet[name])
            except (TypeError, ValueError):
                pass
        return None

    def on_connection(self, interface, topic=pub.AUTO_TOPIC) -> None:
        with self.lock:
            self.interface = interface
            self.state["connected"] = True
            self.state["connected_at"] = time.time()
            self.state["last_error"] = None
            if getattr(interface, "devPath", None):
                self.state["device"] = str(interface.devPath)
        log.info("Meshtastic connected: %s", self.state["device"])
        self._write()

    def on_connection_lost(self, interface=None, topic=pub.AUTO_TOPIC) -> None:
        with self.lock:
            self.state["connected"] = False
        log.warning("Meshtastic connection lost")
        self._write()

    def on_receive(self, packet, interface=None) -> None:
        if not isinstance(packet, dict):
            return
        with self.lock:
            if interface is not None:
                self.interface = interface
            self.state["rx_packets"] = int(self.state.get("rx_packets") or 0) + 1
            self.state["last_packet_at"] = time.time()
            self.state["last_rssi"] = self._metric(packet, "rxRssi", "rx_rssi")
            self.state["last_snr"] = self._metric(packet, "rxSnr", "rx_snr")
            self.state["last_from"] = self._sender_name(packet)
        self._write()

    def on_text(self, packet, interface=None) -> None:
        if not isinstance(packet, dict):
            return
        decoded = packet.get("decoded") if isinstance(packet.get("decoded"), dict) else {}
        text = decoded.get("text")
        if text is None and isinstance(decoded.get("data"), dict):
            text = decoded["data"].get("text")
        if text is None:
            return
        with self.lock:
            if interface is not None:
                self.interface = interface
            self.state["last_message"] = {
                "text": str(text),
                "from": self._sender_name(packet),
                "received_at": time.time(),
                "packet_id": packet.get("id"),
            }
        self._write()

    def run_once(self) -> None:
        kwargs: dict[str, Any] = {}
        if DEVICE:
            kwargs["devPath"] = DEVICE
        interface = meshtastic.serial_interface.SerialInterface(**kwargs)
        self.interface = interface

        if getattr(interface, "devPath", None) is None:
            try:
                interface.close()
            except Exception:
                pass
            raise RuntimeError("Kein Meshtastic-USB-Gerät gefunden")

        with self.lock:
            self.state["connected"] = True
            self.state["connected_at"] = self.state.get("connected_at") or time.time()
            self.state["device"] = str(getattr(interface, "devPath", None) or DEVICE or "auto")
            self.state["last_error"] = None
        self._write()

        while True:
            connected = getattr(interface, "isConnected", None)
            if connected is None or not connected.is_set():
                break
            time.sleep(2)

        try:
            interface.close()
        except Exception:
            pass
        with self.lock:
            self.state["connected"] = False
        self._write()

    def run(self) -> None:
        pub.subscribe(self.on_connection, "meshtastic.connection.established")
        pub.subscribe(self.on_connection_lost, "meshtastic.connection.lost")
        pub.subscribe(self.on_receive, "meshtastic.receive")
        pub.subscribe(self.on_text, "meshtastic.receive.text")
        self._write()

        while True:
            try:
                self.run_once()
            except KeyboardInterrupt:
                break
            except SystemExit as exc:
                with self.lock:
                    self.state["connected"] = False
                    self.state["last_error"] = f"Meshtastic auto-detect stopped: {exc}"
                self._write()
                log.warning("Meshtastic auto-detect failed; retry in %ss", RETRY_SECONDS)
            except Exception as exc:
                with self.lock:
                    self.state["connected"] = False
                    self.state["last_error"] = f"{type(exc).__name__}: {exc}"
                self._write()
                log.warning("Meshtastic unavailable; retry in %ss: %s", RETRY_SECONDS, exc)
            time.sleep(RETRY_SECONDS)


def main() -> None:
    Collector().run()


if __name__ == "__main__":
    main()
