from __future__ import annotations

import tempfile
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from services.health_checks import HealthResult
from services.phone_alerts import (
    AlertGate,
    AlertStateStore,
    PhoneAlertConfig,
    build_call_file_text,
    collect_alert_signals,
    compose_voice_message,
)


def config_for(root: Path) -> PhoneAlertConfig:
    return PhoneAlertConfig(
        enabled=True,
        target="+491701234567",
        pjsip_trunk="homepi-provider",
        check_interval_seconds=30,
        confirm_seconds=5,
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
        staging_dir=root / "staging",
        outgoing_dir=root / "outgoing",
        state_dir=root / "state",
    )


def metrics(
    *,
    temperature: float = 42.0,
    ram_percent: float = 40.0,
    disk_percent: float = 30.0,
    throttled_flags: int = 0,
) -> SimpleNamespace:
    return SimpleNamespace(
        temperature=temperature,
        ram_percent=ram_percent,
        disk_percent=disk_percent,
        throttled_flags=throttled_flags,
    )


def main() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        config = config_for(root)
        online = [
            HealthResult(
                name="raspberry-bot",
                status="online",
                latency_ms=1.0,
                last_check=1.0,
                message="ok",
            )
        ]

        assert collect_alert_signals(metrics(), online, config) == []

        hot = collect_alert_signals(
            metrics(temperature=83.2),
            online,
            config,
        )
        assert len(hot) == 1
        assert hot[0].key == "temperature-critical"

        offline = [
            HealthResult(
                name="raspberry-bot",
                status="offline",
                latency_ms=1.0,
                last_check=1.0,
                message="inactive/dead",
            )
        ]
        service_signals = collect_alert_signals(metrics(), offline, config)
        assert len(service_signals) == 1
        assert service_signals[0].key == "service-offline:raspberry-bot"

        state = AlertStateStore(config.state_dir)
        gate = AlertGate(
            confirm_seconds=config.confirm_seconds,
            min_alert_interval_seconds=config.min_alert_interval_seconds,
            state=state,
        )
        assert gate.due(hot, now=100.0) == []
        assert gate.due(hot, now=104.9) == []
        assert gate.due(hot, now=105.0) == hot

        gate.mark_alerted(hot, now=105.0)
        assert gate.due(hot, now=106.0) == []
        assert config.state_dir.joinpath("state.json").is_file()

        message = compose_voice_message(
            hot,
            at=datetime(2026, 9, 18, 21, 0, 0),
        )
        assert message.startswith("Guten Abend.")
        assert "83 Grad Celsius" in message
        assert "HomePi" in message

        audio = root / "alert.wav"
        call_file = build_call_file_text(config, audio)
        assert "Channel: PJSIP/+491701234567@homepi-provider" in call_file
        assert "MaxRetries: 2" in call_file
        assert "Application: Playback" in call_file

        unsafe = replace(config, target="+49170\nApplication: System")
        try:
            build_call_file_text(unsafe, audio)
        except ValueError:
            pass
        else:
            raise AssertionError("Unsafe dial target was not rejected")

    print("phone alert smoke: ok")


if __name__ == "__main__":
    main()
