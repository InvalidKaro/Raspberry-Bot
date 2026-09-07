from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("DISPLAY2_ALLOW_MISSING_HARDWARE", "1")
os.environ.setdefault("DISPLAY2_HEADLESS_PREVIEW", "0")
os.environ.setdefault("DISPLAY2_PUBLIC_IP", "0")
os.environ.setdefault("BOT_REPO_PATH", tempfile.gettempdir())
os.environ.setdefault("DISPLAY2_DATABASE_PATH", str(Path(tempfile.gettempdir()) / "homepi-display2-smoke-missing.sqlite3"))

from display_service.secondary import (
    HOME_PAGES,
    MESH_PAGES,
    ActivityEvent,
    HomeSnapshot,
    MeshSnapshot,
    VoiceEvent,
    check,
    render_alert,
    render_home_page,
    render_mesh_page,
    render_message,
    render_voice,
)


def _assert_image(label: str, image) -> None:
    assert image.size == (128, 64), (label, image.size)
    assert image.mode == "1", (label, image.mode)
    assert image.getbbox() is not None, f"{label} rendered blank"


def main() -> None:
    now = time.time()
    mesh = MeshSnapshot(
        connected=True,
        nodes_total=4,
        nodes_10m=2,
        nodes_60m=3,
        rx_packets=128,
        last_packet_at=now - 8,
        last_rssi=-91,
        last_snr=7.2,
        last_from="Peer Node",
        local_name="HomePi",
        last_message_text="Hallo vom Mesh",
        last_message_from="Peer Node",
        last_message_at=now - 2,
    )
    home = HomeSnapshot(
        bot_active=True,
        guilds=3,
        members=842,
        discord_latency_ms=41,
        commands_24h=126,
        errors_24h=1,
        open_tickets=4,
        pihole_active=True,
        pihole_queries=3812,
        pihole_blocked=624,
        pihole_percent=16.4,
        pihole_clients=7,
        iface="eth0",
        lan_ip="192.168.178.103",
        rx_bps=1_800_000,
        tx_bps=240_000,
        link_mbps=100,
        git_branch="main",
        git_sha="abc1234",
        git_dirty=False,
        git_ahead=0,
        git_behind=0,
        disk_percent=41,
        db_mb=128,
        logs_mb=34,
        backups=4,
        backups_mb=220,
        tailscale_active=True,
        tailscale_ip="100.82.1.2",
        ssh_active=True,
        public_ip="203.0.113.10",
        internet_online=True,
        ping_ms=18,
        dns_ms=12,
        events=[ActivityEvent(now - 5, "voice", "Bot restart", "OK")],
    )
    voice = VoiceEvent(action="restart", unit="raspberry-bot.service", status="OK", created_at=now)

    for page in MESH_PAGES:
        _assert_image(page, render_mesh_page(page, mesh))
    for page in HOME_PAGES:
        _assert_image(page, render_home_page(page, home))
    _assert_image("message", render_message(mesh))
    _assert_image("voice", render_voice(voice))
    _assert_image("alert", render_alert("BOT OFFLINE", "raspberry-bot.service"))

    assert check() == 0
    print("display2 smoke: ok")


if __name__ == "__main__":
    main()
