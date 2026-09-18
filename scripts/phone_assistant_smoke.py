from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.phone_alerts import AlertSignal, PhoneAlertConfig
from services.phone_incidents import (
    IncidentStore,
    InteractiveAlertConfig,
    build_interactive_call_file,
    process_action_requests,
    restart_unit_for_signals,
)


def phone_config(root: Path) -> PhoneAlertConfig:
    return PhoneAlertConfig(
        enabled=True,
        target="+491701234567",
        pjsip_trunk="homepi-provider",
        check_interval_seconds=30,
        confirm_seconds=90,
        min_alert_interval_seconds=3600,
        temperature_critical=80.0,
        ram_critical=95.0,
        disk_critical=95.0,
        monitored_services=("raspberry-bot",),
        call_max_retries=2,
        call_retry_seconds=60,
        call_wait_seconds=35,
        voice="de",
        voice_speed=145,
        voice_pitch=38,
        voice_amplitude=135,
        audio_dir=root / "audio",
        staging_dir=root / "fallback-staging",
        outgoing_dir=root / "outgoing",
        state_dir=root / "state",
    )


def interactive_config(root: Path) -> InteractiveAlertConfig:
    return InteractiveAlertConfig(
        enabled=True,
        root_dir=root / "shared",
        agi_script=root / "homepi-alert-agi.py",
        escalation_seconds=300,
        max_escalations=2,
        action_timeout_seconds=30,
        restartable_services=(
            "raspberry-bot.service",
            "raspberry-dashboard.service",
            "pihole-FTL.service",
        ),
    )


async def invalid_action_is_rejected(config: InteractiveAlertConfig) -> None:
    config.actions_dir.mkdir(parents=True, exist_ok=True)
    request = config.actions_dir / "bad.request.json"
    request.write_text(
        json.dumps(
            {
                "version": 1,
                "request_id": "bad",
                "action": "restart",
                "unit": "ssh.service",
            }
        ),
        encoding="utf-8",
    )
    processed = await process_action_requests(config)
    assert processed == 1
    result = json.loads(
        config.actions_dir.joinpath("bad.result.json").read_text(
            encoding="utf-8"
        )
    )
    assert result["ok"] is False
    assert "allowlisted" in result["detail"]


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        phone = phone_config(root)
        interactive = interactive_config(root)
        interactive.agi_script.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
        phone.outgoing_dir.mkdir(parents=True, exist_ok=True)

        store = IncidentStore(interactive)
        store.ensure_directories()

        signal = AlertSignal(
            key="service-offline:raspberry-bot",
            summary="Service offline: raspberry-bot",
            spoken="Der Dienst raspberry bot ist nicht erreichbar.",
        )
        restart = restart_unit_for_signals(
            [signal],
            interactive.restartable_services,
        )
        assert restart == "raspberry-bot.service"

        audio = interactive.audio_dir / "alert.wav"
        audio.write_bytes(b"RIFF-test")
        incident = store.create(
            [signal],
            audio_file=audio,
            restart_unit=restart,
            now=100.0,
        )
        assert incident.restart_unit == "raspberry-bot.service"

        call_file = build_interactive_call_file(
            phone,
            interactive,
            incident,
        )
        assert "Channel: PJSIP/+491701234567@homepi-provider" in call_file
        assert "Application: AGI" in call_file
        assert str(interactive.agi_script) in call_file
        assert f"{incident.id}.json" in call_file

        incident.attempts = 1
        incident.last_queued_at = 100.0
        store.save(incident)
        assert store.reconcile({signal.key}, now=399.9) == []
        due = store.reconcile({signal.key}, now=400.0)
        assert len(due) == 1
        assert due[0].id == incident.id

        interactive.acknowledgements_dir.joinpath(
            f"{incident.id}.ack"
        ).touch()
        assert store.consume_acknowledgements() == 1
        loaded = store.load(incident.id)
        assert loaded is not None
        assert loaded.acknowledged is True
        assert loaded.closed is True

        asyncio.run(invalid_action_is_rejected(interactive))

    print("phone assistant smoke: ok")


if __name__ == "__main__":
    main()
