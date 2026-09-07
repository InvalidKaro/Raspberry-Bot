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
MAX_MESSAGES = max(5, min(100, int(os.getenv("MESHTASTIC_MAX_MESSAGES", "25"))))

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
            "nodes": [],
            "rx_packets": 0,
            "last_packet_at": 0,
            "last_rssi": None,
            "last_snr": None,
            "last_from": "",
            "last_message": {"text": "", "from": "", "received_at": 0, "packet_id": None},
            "messages": [],
            "local": {"name": "", "short_name": "", "id": "", "firmware": "", "hardware": ""},
            "radio": {
                "region": "",
                "modem_preset": "",
                "primary_channel": "",
                "tx_enabled": None,
                "hop_limit": None,
            },
            "updated_at": "",
        }
        self._restore_previous_state()

    def _restore_previous_state(self) -> None:
        try:
            previous = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return
        if not isinstance(previous, dict):
            return

        for key in (
            "rx_packets",
            "last_packet_at",
            "last_rssi",
            "last_snr",
            "last_from",
            "last_message",
            "messages",
            "nodes",
            "nodes_total",
            "nodes_active_10m",
            "nodes_active_60m",
            "local",
            "radio",
        ):
            if key in previous:
                self.state[key] = previous[key]
        if isinstance(self.state.get("messages"), list):
            self.state["messages"] = self.state["messages"][-MAX_MESSAGES:]
        else:
            self.state["messages"] = []
        self.state["connected"] = False
        self.state["connected_at"] = 0
        self.state["last_error"] = None

    @staticmethod
    def _enum_name(message: Any, field_name: str) -> str:
        if message is None:
            return ""
        try:
            value = getattr(message, field_name)
        except (AttributeError, TypeError):
            return ""
        descriptor = getattr(message, "DESCRIPTOR", None)
        try:
            field = descriptor.fields_by_name[field_name]
            enum_value = field.enum_type.values_by_number.get(int(value))
            if enum_value is not None:
                return str(enum_value.name)
        except (AttributeError, KeyError, TypeError, ValueError):
            pass
        name = getattr(value, "name", None)
        return str(name or value or "")

    @staticmethod
    def _number(value: Any) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _my_node_num(self) -> int | None:
        my_num = getattr(getattr(self.interface, "myInfo", None), "my_node_num", None)
        try:
            return int(my_num) if my_num is not None else None
        except (TypeError, ValueError):
            return None

    def _refresh_nodes(self) -> None:
        iface = self.interface
        nodes = getattr(iface, "nodes", None) if iface is not None else None
        if not isinstance(nodes, dict):
            return

        now = time.time()
        my_num = self._my_node_num()
        rows: list[dict[str, Any]] = []
        active_10m = 0
        active_60m = 0

        for key, node in nodes.items():
            if not isinstance(node, dict):
                continue
            node_num = node.get("num")
            try:
                node_num_int = int(node_num) if node_num is not None else None
            except (TypeError, ValueError):
                node_num_int = None
            is_local = my_num is not None and node_num_int == my_num

            try:
                last_heard = float(node.get("lastHeard") or 0)
            except (TypeError, ValueError):
                last_heard = 0
            if not is_local and last_heard and now - last_heard <= 600:
                active_10m += 1
            if not is_local and last_heard and now - last_heard <= 3600:
                active_60m += 1

            user = node.get("user") if isinstance(node.get("user"), dict) else {}
            metrics = node.get("deviceMetrics") if isinstance(node.get("deviceMetrics"), dict) else {}
            row = {
                "id": str(user.get("id") or key or ""),
                "num": node_num_int,
                "name": str(user.get("longName") or user.get("shortName") or user.get("id") or key or ""),
                "short_name": str(user.get("shortName") or ""),
                "hardware": str(user.get("hwModel") or ""),
                "last_heard": last_heard,
                "snr": self._number(node.get("snr")),
                "hops_away": node.get("hopsAway"),
                "battery_level": metrics.get("batteryLevel"),
                "voltage": self._number(metrics.get("voltage")),
                "channel_utilization": self._number(metrics.get("channelUtilization")),
                "air_util_tx": self._number(metrics.get("airUtilTx")),
                "is_local": is_local,
            }
            rows.append(row)

            if is_local:
                self.state["local"]["name"] = str(user.get("longName") or user.get("shortName") or "")
                self.state["local"]["short_name"] = str(user.get("shortName") or "")
                self.state["local"]["id"] = str(user.get("id") or "")
                self.state["local"]["hardware"] = str(user.get("hwModel") or "")

        rows.sort(key=lambda row: (bool(row.get("is_local")), -(float(row.get("last_heard") or 0))))
        peers = [row for row in rows if not row.get("is_local")]
        self.state["nodes"] = rows
        self.state["nodes_total"] = len(peers)
        self.state["nodes_active_10m"] = active_10m
        self.state["nodes_active_60m"] = active_60m

        metadata = getattr(iface, "metadata", None)
        firmware = getattr(metadata, "firmware_version", None)
        if firmware:
            self.state["local"]["firmware"] = str(firmware)

    def _refresh_radio(self) -> None:
        iface = self.interface
        local_node = getattr(iface, "localNode", None) if iface is not None else None
        local_config = getattr(local_node, "localConfig", None)
        lora = getattr(local_config, "lora", None)
        if lora is not None:
            radio = self.state["radio"]
            radio["region"] = self._enum_name(lora, "region")
            radio["modem_preset"] = self._enum_name(lora, "modem_preset")
            tx_enabled = getattr(lora, "tx_enabled", None)
            radio["tx_enabled"] = bool(tx_enabled) if tx_enabled is not None else None
            hop_limit = getattr(lora, "hop_limit", None)
            try:
                radio["hop_limit"] = int(hop_limit) if hop_limit is not None else None
            except (TypeError, ValueError):
                radio["hop_limit"] = None

        channels = getattr(local_node, "channels", None)
        if channels is None:
            return
        try:
            iterable = list(channels)
        except TypeError:
            return
        for channel in iterable:
            if self._enum_name(channel, "role") != "PRIMARY":
                continue
            settings = getattr(channel, "settings", None)
            name = getattr(settings, "name", "") if settings is not None else ""
            self.state["radio"]["primary_channel"] = str(name or "LongFast")
            break

    def _write(self) -> None:
        with self.lock:
            self._refresh_nodes()
            self._refresh_radio()
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
        received_at = time.time()
        message = {
            "text": str(text),
            "from": self._sender_name(packet),
            "received_at": received_at,
            "packet_id": packet.get("id"),
        }
        with self.lock:
            if interface is not None:
                self.interface = interface
            self.state["last_message"] = message
            messages = self.state.get("messages")
            if not isinstance(messages, list):
                messages = []
            packet_id = message.get("packet_id")
            if packet_id is None or not any(item.get("packet_id") == packet_id for item in messages if isinstance(item, dict)):
                messages.append(message)
            self.state["messages"] = messages[-MAX_MESSAGES:]
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
