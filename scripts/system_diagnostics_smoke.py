from __future__ import annotations

from services.system_diagnostics import diagnose_system
from services.system_metrics import SystemMetrics


def _metrics(**overrides):
    data = dict(
        cpu_percent=12.0,
        cpu_average_30s=15.0,
        cpu_average_5m=18.0,
        sample_interval_seconds=15,
        sample_age_seconds=1.0,
        bot_cpu_percent=1.0,
        dashboard_cpu_percent=1.0,
        temperature=45.0,
        ram_percent=40.0,
        ram_used=400,
        ram_total=1000,
        ram_available=600,
        swap_percent=0.0,
        swap_used=0,
        swap_total=1000,
        disk_percent=30.0,
        disk_used=300,
        disk_total=1000,
        load_1m=0.1,
        load_5m=0.1,
        load_15m=0.1,
        network_rx=0,
        network_tx=0,
        network_rx_rate=0.0,
        network_tx_rate=0.0,
        bot_memory=10,
        dashboard_memory=10,
        uptime_seconds=3600,
        throttled_flags=0,
        cpu_frequency_mhz=1200.0,
        pihole_active=True,
        pihole_blocking=True,
    )
    data.update(overrides)
    return SystemMetrics(**data)


def main() -> None:
    healthy = diagnose_system(_metrics())
    assert healthy.healthy
    assert healthy.primary is None

    undervoltage = diagnose_system(_metrics(throttled_flags=(1 << 16) | (1 << 18)))
    assert not undervoltage.healthy
    assert undervoltage.primary is not None
    assert undervoltage.primary.id in {"undervoltage_history", "throttling_history"}
    bonuses = undervoltage.command_bonuses()
    assert "system now" in bonuses
    assert bonuses["system now"][0] > 0

    hot = diagnose_system(_metrics(temperature=84.0, throttled_flags=(1 << 3)))
    assert hot.primary is not None
    assert hot.primary.severity == "critical"
    assert "system processes" in hot.command_bonuses()

    memory = diagnose_system(_metrics(ram_percent=96.0))
    assert memory.primary is not None
    assert memory.primary.id == "ram_high"
    assert memory.primary.severity == "critical"
    assert "system memory" in memory.command_bonuses()


if __name__ == "__main__":
    main()
