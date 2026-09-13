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
    errors_24h: int = 0
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
    "errors": 0.05,
}


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


def _error_score(errors_24h: int) -> float:
    errors = max(0, int(errors_24h))
    if errors == 0:
        return 100.0
    if errors >= 20:
        return 0.0
    return max(0.0, 100.0 - errors * 5.0)


def grade_for(score: int) -> str:
    if score >= 90:
        return "excellent"
    if score >= 75:
        return "good"
    if score >= 60:
        return "degraded"
    return "critical"


def calculate_server_score(data: ScoreInput) -> ScoreBreakdown:
    """Return a stable 0-100 score from smoothed metrics.

    The caller should pass rolling CPU/RAM values where available. The scoring
    function deliberately uses broad soft/hard ranges so a brief CPU spike does
    not collapse the entire HomePi health score.
    """

    components = {
        "cpu": _inverse_usage_score(data.cpu_percent, soft=45, hard=95),
        "ram": _inverse_usage_score(data.ram_percent, soft=60, hard=96),
        "temperature": _temperature_score(data.temperature_c),
        "disk": _inverse_usage_score(data.disk_percent, soft=70, hard=97),
        "services": _clamp(data.services_online_ratio * 100.0),
        "network": 100.0 if data.network_ok else 0.0,
        "errors": _error_score(data.errors_24h),
    }
    weighted = sum(components[name] * WEIGHTS[name] for name in WEIGHTS)
    score = int(round(_clamp(weighted)))
    return ScoreBreakdown(
        score=score,
        grade=grade_for(score),
        components={name: round(value, 1) for name, value in components.items()},
        weights=dict(WEIGHTS),
    )
