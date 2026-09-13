from __future__ import annotations

from services.action_registry import action_ids_for_command, action_specs_for_command
from services.server_score import ScoreInput, calculate_server_score


def test_action_contexts() -> None:
    admin = action_ids_for_command("admin healthcheck")
    media = action_ids_for_command("media radio")
    generic = action_ids_for_command("userinfo")

    assert admin[:3] == ("system_status", "quick_check", "diagnostics")
    assert "related" in media
    assert generic == ("related", "control_center")
    assert len(action_specs_for_command("admin diagnose")) <= 5


def test_server_score() -> None:
    healthy = calculate_server_score(
        ScoreInput(
            cpu_percent=18,
            ram_percent=52,
            temperature_c=42,
            disk_percent=35,
            services_online_ratio=1.0,
            network_ok=True,
            errors_24h=0,
            uptime_seconds=86400,
        )
    )
    stressed = calculate_server_score(
        ScoreInput(
            cpu_percent=90,
            ram_percent=93,
            temperature_c=80,
            disk_percent=92,
            services_online_ratio=0.5,
            network_ok=False,
            errors_24h=12,
            uptime_seconds=600,
        )
    )
    short_cpu_spike = calculate_server_score(
        ScoreInput(
            cpu_percent=75,
            ram_percent=52,
            temperature_c=42,
            disk_percent=35,
            services_online_ratio=1.0,
            network_ok=True,
            errors_24h=0,
            uptime_seconds=86400,
        )
    )

    assert 0 <= stressed.score < healthy.score <= 100
    assert healthy.grade in {"excellent", "good"}
    assert short_cpu_spike.score >= 80
    assert abs(sum(healthy.weights.values()) - 1.0) < 0.0001


def main() -> None:
    test_action_contexts()
    test_server_score()
    print("control-center architecture smoke: ok")


if __name__ == "__main__":
    main()
