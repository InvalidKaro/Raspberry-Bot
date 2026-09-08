from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import homepi_intelligence as intel


def main() -> None:
    root = Path(tempfile.mkdtemp(prefix="homepi-intel-smoke-"))
    intel.BLACKBOX_DB_PATH = root / "blackbox.sqlite3"
    intel.STATE_PATH = root / "state.json"
    intel.init_db()

    intel.log_event("internet_down", "Internet ausgefallen", "test", "warning")
    intel.log_event("service_down", "Bot down", "test", "warning")
    summary = intel.blackbox_summary(24)
    assert summary["internet_outages"] == 1, summary
    assert summary["service_crashes"] == 1, summary

    score = intel.calculate_score(
        {
            "cpu_percent": 12,
            "ram_percent": 42,
            "disk_percent": 38,
            "temperature": 44,
            "internet_online": True,
            "latency_ms": 18,
            "dns_ok": True,
        },
        {"a.service": "active", "b.service": "active"},
        {"internet_outages": 0, "service_crashes": 0, "bot_errors": 0, "reboots": 0},
    )
    assert score["overall"] >= 95, score
    assert score["status"] == "GREEN", score

    warnings = intel.parse_nina_payload(
        [
            {
                "id": "demo-warning",
                "payload": {
                    "data": {
                        "headline": "Amtliche WARNUNG vor STURM",
                        "provider": "DWD",
                        "severity": "Severe",
                        "urgency": "Immediate",
                        "msgType": "Alert",
                        "valid": True,
                    }
                },
                "sent": "2026-09-08T12:00:00+02:00",
                "expires": "2099-09-08T18:00:00+02:00",
            }
        ],
        now=time.time(),
    )
    assert len(warnings) == 1, warnings
    assert warnings[0]["severity_rank"] == 3, warnings
    assert warnings[0]["provider"] == "DWD", warnings

    print("homepi intelligence smoke: ok")


if __name__ == "__main__":
    main()
