from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class ScoreInput:
    cpu_percent: float
    ram_percent: float
    temperature_c: float | None
    disk_percent: float
    services_online_ratio: float
    network_ok: bool
    latency_ms: float | None = None
    dns_ok: bool = True
    internet_outages_24h: int = 0
    service_crashes_24h: int = 0
    bot_errors_24h: int = 0
    reboots_24h: int = 0
    uptime_seconds: int = 0


@dataclass(frozen=True, slots=True)
class ScoreBreakdown:
    score: int
    grade: str
    components: dict[str, float]
    weights: dict[str, float]

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


WEIGHTS: dict[str, float] = {
    "cpu": 0.18,
    "ram": 0.22,
    "temperature": 0.15,
    "disk": 0.12,
    "services": 0.20,
    "network": 0.08,
    "stability": 0.05,
}

SYSTEM_COMPONENTS = ("cpu", "ram", "temperature", "disk")


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, float(value)))


def _inverse_usage_score(percent: float, *, soft: float, hard: float) -> float:
    value = _clamp(percent)
    if value <= soft:
        return 100.0
    if value >= hard:
        return 0.0
    return 100.0 * (hard - value) / (hard - soft)


def _temperature_score(value: float | None) -> float:
    if value is None:
        return 70.0
    if value <= 55:
        return 100.0
    if value >= 85:
        return 0.0
    return 100.0 * (85.0 - value) / 30.0


def _network_score(data: ScoreInput) -> float:
    if not data.network_ok:
        return 0.0
    score = 100.0
    if data.latency_ms is not None:
        latency = max(0.0, float(data.latency_ms))
        if latency > 80:
            score -= min(45.0, (latency - 80.0) * 0.22)
    if not data.dns_ok:
        score -= 35.0
    return _clamp(score)


def _stability_score(data: ScoreInput) -> float:
    score = 100.0
    score -= max(0, int(data.internet_outages_24h)) * 12.0
    score -= max(0, int(data.service_crashes_24h)) * 10.0
    score -= max(0, int(data.bot_errors_24h)) * 3.0
    score -= max(0, int(data.reboots_24h)) * 4.0
    return _clamp(score)


def grade_for(score: int) -> str:
    if score >= 90:
        return "excellent"
    if score >= 75:
        return "good"
    if score >= 60:
        return "degraded"
    return "critical"


def status_for(score: int) -> str:
    if score >= 90:
        return "GREEN"
    if score >= 75:
        return "YELLOW"
    if score >= 55:
        return "ORANGE"
    return "RED"


def calculate_server_score(data: ScoreInput) -> ScoreBreakdown:
    """Return a stable, explainable 0-100 HomePi score.

    Broad soft/hard thresholds keep short Raspberry-Pi CPU spikes from dominating
    the result. Service availability has the largest single operational weight,
    while recurring incidents are reflected separately as stability.
    """

    components = {
        "cpu": _inverse_usage_score(data.cpu_percent, soft=45, hard=95),
        "ram": _inverse_usage_score(data.ram_percent, soft=60, hard=96),
        "temperature": _temperature_score(data.temperature_c),
        "disk": _inverse_usage_score(data.disk_percent, soft=70, hard=97),
        "services": _clamp(data.services_online_ratio * 100.0),
        "network": _network_score(data),
        "stability": _stability_score(data),
    }
    weighted = sum(components[name] * WEIGHTS[name] for name in WEIGHTS)
    score = int(round(_clamp(weighted)))
    return ScoreBreakdown(
        score=score,
        grade=grade_for(score),
        components={name: round(value, 1) for name, value in components.items()},
        weights=dict(WEIGHTS),
    )


def compatibility_breakdown(data: ScoreInput) -> dict[str, int | str]:
    """Expose the legacy Intelligence score fields from the shared calculation."""

    result = calculate_server_score(data)
    components = result.components
    system_weight = sum(WEIGHTS[name] for name in SYSTEM_COMPONENTS)
    system = sum(components[name] * WEIGHTS[name] for name in SYSTEM_COMPONENTS) / system_weight
    return {
        "overall": result.score,
        "status": status_for(result.score),
        "system": round(system),
        "network": round(components["network"]),
        "services": round(components["services"]),
        "stability": round(components["stability"]),
    }
