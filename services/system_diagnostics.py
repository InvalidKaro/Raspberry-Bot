from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from services.system_metrics import SystemMetrics

Severity = Literal["info", "warning", "critical"]


@dataclass(frozen=True, slots=True)
class Finding:
    id: str
    severity: Severity
    title: str
    cause: str
    confidence: float
    evidence: tuple[str, ...]
    recommendations: tuple[str, ...]
    commands: tuple[str, ...]

    @property
    def severity_rank(self) -> int:
        return {"info": 0, "warning": 1, "critical": 2}[self.severity]


@dataclass(frozen=True, slots=True)
class SystemDiagnosis:
    findings: tuple[Finding, ...]
    healthy: bool
    primary: Finding | None

    def command_bonuses(self) -> dict[str, tuple[float, str]]:
        bonuses: dict[str, tuple[float, str]] = {}
        for finding in self.findings:
            base = 180.0 if finding.severity == "critical" else 135.0 if finding.severity == "warning" else 55.0
            confidence = max(0.25, min(1.0, finding.confidence))
            for index, command in enumerate(finding.commands):
                score = base * confidence - index * 12
                current = bonuses.get(command.casefold())
                if current is None or score > current[0]:
                    bonuses[command.casefold()] = (score, finding.title)
        return bonuses


def _bit(flags: int, bit: int) -> bool:
    return bool(flags & (1 << bit))


def diagnose_system(
    metrics: SystemMetrics,
    *,
    temp_warning: float = 70.0,
    temp_critical: float = 80.0,
    ram_warning: float = 80.0,
    disk_warning: float = 85.0,
) -> SystemDiagnosis:
    findings: list[Finding] = []
    flags = int(metrics.throttled_flags)

    undervoltage_now = _bit(flags, 0)
    frequency_now = _bit(flags, 1)
    throttled_now = _bit(flags, 2)
    thermal_now = _bit(flags, 3)
    undervoltage_seen = _bit(flags, 16)
    frequency_seen = _bit(flags, 17)
    throttled_seen = _bit(flags, 18)
    thermal_seen = _bit(flags, 19)

    if undervoltage_now:
        findings.append(
            Finding(
                id="undervoltage_now",
                severity="critical" if throttled_now else "warning",
                title="Unterspannung ist aktuell aktiv",
                cause="Stromversorgung",
                confidence=0.98,
                evidence=(
                    "Firmware meldet das aktuelle Undervoltage-Bit.",
                    "Der Pi bekommt momentan nicht zuverlässig genug Versorgungsspannung.",
                ),
                recommendations=(
                    "Netzteil und Micro-USB-Kabel prüfen bzw. testweise tauschen.",
                    "USB-Verbraucher testweise abziehen und GPIO-Verbraucher kontrollieren.",
                    "Nach der Änderung erneut prüfen; historische Bits bleiben bis zum Neustart gesetzt.",
                ),
                commands=("system diagnose", "system now", "system processes", "system health"),
            )
        )
    elif undervoltage_seen:
        evidence = ["Unterspannung wurde seit dem letzten Boot mindestens einmal erkannt."]
        if metrics.temperature is not None and metrics.temperature < temp_warning:
            evidence.append(f"Temperatur ist mit {metrics.temperature:.1f} °C unauffällig; Hitze ist als Hauptursache unwahrscheinlich.")
        if not throttled_now:
            evidence.append("Der Pi ist aktuell nicht aktiv gedrosselt.")
        findings.append(
            Finding(
                id="undervoltage_history",
                severity="warning",
                title="Unterspannung trat seit dem Boot auf",
                cause="kurzer Spannungseinbruch / Versorgung",
                confidence=0.94,
                evidence=tuple(evidence),
                recommendations=(
                    "Auf Netzteil, Kabel und Lastspitzen achten.",
                    "USB-Geräte einzeln testen, wenn der Fehler wiederkehrt.",
                    "Nach einem kontrollierten Neustart beobachten, ob das Bit erneut gesetzt wird.",
                ),
                commands=("system diagnose", "system now", "system processes", "system health"),
            )
        )

    if thermal_now:
        findings.append(
            Finding(
                id="thermal_now",
                severity="critical" if metrics.temperature is not None and metrics.temperature >= temp_critical else "warning",
                title="Temperaturlimit ist aktuell aktiv",
                cause="Kühlung / Umgebungstemperatur",
                confidence=0.98,
                evidence=(
                    "Firmware meldet das aktuelle Soft-Temperature-Limit.",
                    f"Gemessene CPU-Temperatur: {metrics.temperature:.1f} °C." if metrics.temperature is not None else "Temperatursensorwert ist nicht verfügbar.",
                ),
                recommendations=(
                    "Luftstrom, Lüfter und Kühlkörper prüfen.",
                    "CPU-intensive Prozesse prüfen.",
                    "Gehäuseöffnungen und Umgebungstemperatur kontrollieren.",
                ),
                commands=("system diagnose", "system processes", "system graph", "system now"),
            )
        )
    elif thermal_seen:
        findings.append(
            Finding(
                id="thermal_history",
                severity="warning",
                title="Temperaturlimit trat seit dem Boot auf",
                cause="frühere thermische Last",
                confidence=0.92,
                evidence=(
                    "Das historische Soft-Temperature-Limit-Bit ist gesetzt.",
                    f"Aktuelle Temperatur: {metrics.temperature:.1f} °C." if metrics.temperature is not None else "Aktuelle Temperatur nicht verfügbar.",
                ),
                recommendations=("Temperaturverlauf prüfen.", "Lüfter/Kühlkörper auf sicheren Sitz prüfen."),
                commands=("system graph", "system diagnose", "system processes", "system now"),
            )
        )

    if throttled_now or frequency_now:
        causes: list[str] = []
        if undervoltage_now:
            causes.append("Unterspannung")
        if thermal_now:
            causes.append("Temperaturlimit")
        cause = " + ".join(causes) if causes else "Firmware-/Leistungsbegrenzung"
        findings.append(
            Finding(
                id="throttling_now",
                severity="critical" if throttled_now else "warning",
                title="CPU-Leistung ist aktuell begrenzt",
                cause=cause,
                confidence=0.96 if causes else 0.72,
                evidence=(
                    "Throttling- bzw. Frequency-Cap-Bit ist aktuell gesetzt.",
                    f"Aktueller CPU-Takt: {metrics.cpu_frequency_mhz:.0f} MHz." if metrics.cpu_frequency_mhz is not None else "CPU-Takt konnte nicht gelesen werden.",
                ),
                recommendations=("Primäre Ursache zuerst beheben.", "Danach Systemstatus erneut prüfen."),
                commands=("system diagnose", "system now", "system processes", "system health"),
            )
        )
    elif throttled_seen or frequency_seen:
        causes = []
        if undervoltage_seen:
            causes.append("Unterspannung")
        if thermal_seen:
            causes.append("Temperatur")
        findings.append(
            Finding(
                id="throttling_history",
                severity="warning",
                title="Leistungsbegrenzung trat seit dem Boot auf",
                cause=" / ".join(causes) if causes else "frühere Last- oder Versorgungsgrenze",
                confidence=0.86 if causes else 0.65,
                evidence=("Historisches Throttling/Frequency-Cap-Bit ist gesetzt.", "Aktuell ist keine Leistungsbegrenzung aktiv."),
                recommendations=("Primäre historische Ursache prüfen.", "Nach einem Neustart beobachten, ob der Zustand erneut auftritt."),
                commands=("system diagnose", "system graph", "system now", "system health"),
            )
        )

    if metrics.temperature is not None and metrics.temperature >= temp_warning and not thermal_now:
        findings.append(
            Finding(
                id="temperature_high",
                severity="critical" if metrics.temperature >= temp_critical else "warning",
                title="CPU-Temperatur ist erhöht",
                cause="Systemlast oder Kühlung",
                confidence=0.90,
                evidence=(f"CPU-Temperatur: {metrics.temperature:.1f} °C.", f"Warnschwelle: {temp_warning:.1f} °C."),
                recommendations=("Top-Prozesse prüfen.", "Kühlung und Luftstrom kontrollieren."),
                commands=("system processes", "system graph", "system diagnose", "system now"),
            )
        )

    if metrics.ram_percent >= ram_warning:
        findings.append(
            Finding(
                id="ram_high",
                severity="critical" if metrics.ram_percent >= 95 else "warning",
                title="RAM-Auslastung ist hoch",
                cause="Speicherdruck durch laufende Prozesse",
                confidence=0.95,
                evidence=(f"RAM belegt: {metrics.ram_percent:.1f}%.", f"Swap belegt: {metrics.swap_percent:.1f}%."),
                recommendations=("Speicherintensive Prozesse identifizieren.", "Unnötige Services erst nach Prüfung beenden."),
                commands=("system memory", "system processes", "system diagnose", "system now"),
            )
        )

    if metrics.cpu_average_30s >= 80:
        findings.append(
            Finding(
                id="cpu_high",
                severity="critical" if metrics.cpu_average_30s >= 95 else "warning",
                title="CPU-Last ist anhaltend hoch",
                cause="rechenintensive Prozesse",
                confidence=0.94,
                evidence=(f"30-Sekunden-Mittel: {metrics.cpu_average_30s:.1f}%.", f"5-Minuten-Mittel: {metrics.cpu_average_5m:.1f}%."),
                recommendations=("Top-Prozesse prüfen.", "Bei dauerhaft hoher Last betroffenen Service untersuchen."),
                commands=("system processes", "system graph", "system diagnose", "system now"),
            )
        )

    if metrics.disk_percent >= disk_warning:
        findings.append(
            Finding(
                id="disk_high",
                severity="critical" if metrics.disk_percent >= 95 else "warning",
                title="Speicherplatz wird knapp",
                cause="hohe Root-Dateisystem-Auslastung",
                confidence=0.99,
                evidence=(f"Root-Dateisystem belegt: {metrics.disk_percent:.1f}%.",),
                recommendations=("Logs, Caches und große Dateien prüfen.", "Vor dem Löschen Ursache und benötigte Dateien identifizieren."),
                commands=("system storage", "system diagnose", "system now"),
            )
        )

    if not metrics.pihole_active:
        findings.append(
            Finding(
                id="pihole_inactive",
                severity="warning",
                title="Pi-hole FTL ist nicht aktiv",
                cause="Dienst gestoppt oder nicht erreichbar",
                confidence=0.90,
                evidence=("Der aktuelle System-Snapshot meldet Pi-hole FTL als inaktiv.",),
                recommendations=("Pi-hole-Details prüfen.", "Service-Logs prüfen, bevor neu gestartet wird."),
                commands=("system pihole", "system diagnose", "system health"),
            )
        )

    findings.sort(key=lambda finding: (-finding.severity_rank, -finding.confidence, finding.id))
    primary = findings[0] if findings else None
    return SystemDiagnosis(findings=tuple(findings), healthy=not findings, primary=primary)


def compact_diagnosis(diagnosis: SystemDiagnosis, *, max_findings: int = 5) -> str:
    if diagnosis.healthy:
        return "Keine auffälligen Systemzustände erkannt."
    icons = {"critical": "🔴", "warning": "🟠", "info": "🔵"}
    return "\n".join(
        f"{icons[f.severity]} **{f.title}** · Ursache: {f.cause} · Sicherheit {f.confidence * 100:.0f}%"
        for f in diagnosis.findings[:max_findings]
    )
