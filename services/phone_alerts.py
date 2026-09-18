from __future__ import annotations

import grp
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Sequence

from services.health_checks import HealthResult
from services.system_metrics import SystemMetrics

logger = logging.getLogger(__name__)

_DIAL_TARGET_RE = re.compile(r"^[+0-9*#]{3,32}$")
_TRUNK_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_CONTEXT_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")

SAFE_RECOVERY_UNITS = frozenset(
    {
        "homepi-flight-radar",
        "pihole-FTL",
        "raspberry-bot",
        "raspberry-dashboard",
        "raspberry-display",
        "raspberry-display2",
        "raspberry-intelligence",
        "raspberry-meshtastic",
    }
)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    return max(minimum, min(maximum, value))


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be numeric") from exc
    return max(minimum, min(maximum, value))


def _single_line(value: str, name: str) -> str:
    if "\n" in value or "\r" in value:
        raise ValueError(f"{name} must be a single line")
    return value


@dataclass(frozen=True, slots=True)
class PhoneAlertConfig:
    enabled: bool
    target: str
    pjsip_trunk: str
    check_interval_seconds: int
    request_poll_seconds: int
    confirm_seconds: int
    min_alert_interval_seconds: int
    recovery_cooldown_seconds: int
    temperature_critical: float
    ram_critical: float
    disk_critical: float
    monitored_services: tuple[str, ...]
    call_max_retries: int
    call_retry_seconds: int
    call_wait_seconds: int
    interactive: bool
    allow_recovery: bool
    dialplan_context: str
    voice: str
    voice_speed: int
    voice_pitch: int
    voice_amplitude: int
    audio_dir: Path
    staging_dir: Path
    outgoing_dir: Path
    state_dir: Path
    request_dir: Path
    result_dir: Path
    status_path: Path

    @classmethod
    def from_env(cls) -> "PhoneAlertConfig":
        raw_services = os.getenv(
            "HOMEPI_ALERT_MONITORED_SERVICES",
            "raspberry-bot,raspberry-dashboard,pihole-FTL",
        )
        services = tuple(
            value.strip()
            for value in raw_services.split(",")
            if value.strip()
        )
        state_dir = Path(
            os.getenv("STATE_DIRECTORY")
            or os.getenv("HOMEPI_ALERT_STATE_DIR")
            or str(Path.home() / ".local" / "state" / "homepi-alerts")
        )
        return cls(
            enabled=_env_bool("HOMEPI_ALERTS_ENABLED", False),
            target=os.getenv("HOMEPI_ALERT_TARGET", "").strip(),
            pjsip_trunk=os.getenv("HOMEPI_ALERT_PJSIP_TRUNK", "homepi-provider").strip(),
            check_interval_seconds=_env_int(
                "HOMEPI_ALERT_CHECK_INTERVAL_SECONDS", 30, 10, 600
            ),
            request_poll_seconds=_env_int(
                "HOMEPI_ALERT_REQUEST_POLL_SECONDS", 2, 1, 30
            ),
            confirm_seconds=_env_int("HOMEPI_ALERT_CONFIRM_SECONDS", 90, 0, 3600),
            min_alert_interval_seconds=_env_int(
                "HOMEPI_ALERT_MIN_INTERVAL_SECONDS", 3600, 60, 86400
            ),
            recovery_cooldown_seconds=_env_int(
                "HOMEPI_ALERT_RECOVERY_COOLDOWN_SECONDS", 300, 60, 86400
            ),
            temperature_critical=_env_float(
                "HOMEPI_ALERT_TEMP_CRITICAL", 80.0, 50.0, 120.0
            ),
            ram_critical=_env_float(
                "HOMEPI_ALERT_RAM_CRITICAL", 95.0, 50.0, 100.0
            ),
            disk_critical=_env_float(
                "HOMEPI_ALERT_DISK_CRITICAL", 95.0, 50.0, 100.0
            ),
            monitored_services=services,
            call_max_retries=_env_int("HOMEPI_ALERT_CALL_MAX_RETRIES", 2, 0, 10),
            call_retry_seconds=_env_int(
                "HOMEPI_ALERT_CALL_RETRY_SECONDS", 60, 15, 1800
            ),
            call_wait_seconds=_env_int(
                "HOMEPI_ALERT_CALL_WAIT_SECONDS", 35, 10, 120
            ),
            interactive=_env_bool("HOMEPI_ALERT_INTERACTIVE", True),
            allow_recovery=_env_bool("HOMEPI_ALERT_ALLOW_RECOVERY", False),
            dialplan_context=os.getenv(
                "HOMEPI_ALERT_DIALPLAN_CONTEXT", "homepi-alert"
            ).strip(),
            voice=os.getenv("HOMEPI_ALERT_VOICE", "de").strip() or "de",
            voice_speed=_env_int("HOMEPI_ALERT_VOICE_SPEED", 145, 80, 250),
            voice_pitch=_env_int("HOMEPI_ALERT_VOICE_PITCH", 38, 0, 99),
            voice_amplitude=_env_int(
                "HOMEPI_ALERT_VOICE_AMPLITUDE", 135, 0, 200
            ),
            audio_dir=Path(
                os.getenv("HOMEPI_ALERT_AUDIO_DIR", "/var/tmp/homepi-alerts")
            ),
            staging_dir=Path(
                os.getenv(
                    "HOMEPI_ALERT_STAGING_DIR",
                    "/var/spool/asterisk/.homepi-staging",
                )
            ),
            outgoing_dir=Path(
                os.getenv(
                    "HOMEPI_ALERT_OUTGOING_DIR",
                    "/var/spool/asterisk/outgoing",
                )
            ),
            state_dir=state_dir,
            request_dir=Path(
                os.getenv(
                    "HOMEPI_ALERT_REQUEST_DIR",
                    str(state_dir / "requests"),
                )
            ),
            result_dir=Path(
                os.getenv(
                    "HOMEPI_ALERT_RESULT_DIR",
                    str(state_dir / "results"),
                )
            ),
            status_path=Path(
                os.getenv(
                    "HOMEPI_ALERT_STATUS_PATH",
                    str(state_dir / "status.json"),
                )
            ),
        )

    def validate_for_calling(self) -> None:
        if not self.target:
            raise ValueError("HOMEPI_ALERT_TARGET is empty")
        if not _DIAL_TARGET_RE.fullmatch(self.target):
            raise ValueError(
                "HOMEPI_ALERT_TARGET may contain only digits, +, * and #"
            )
        if not _TRUNK_RE.fullmatch(self.pjsip_trunk):
            raise ValueError("HOMEPI_ALERT_PJSIP_TRUNK contains invalid characters")
        if not _CONTEXT_RE.fullmatch(self.dialplan_context):
            raise ValueError(
                "HOMEPI_ALERT_DIALPLAN_CONTEXT contains invalid characters"
            )
        for name, value in (
            ("HOMEPI_ALERT_AUDIO_DIR", str(self.audio_dir)),
            ("HOMEPI_ALERT_REQUEST_DIR", str(self.request_dir)),
            ("HOMEPI_ALERT_RESULT_DIR", str(self.result_dir)),
            ("HOMEPI_ALERT_STATUS_PATH", str(self.status_path)),
        ):
            _single_line(value, name)


@dataclass(frozen=True, slots=True)
class AlertSignal:
    key: str
    summary: str
    spoken: str
    severity: int = 3


def collect_alert_signals(
    metrics: SystemMetrics,
    service_results: Iterable[HealthResult],
    config: PhoneAlertConfig,
) -> list[AlertSignal]:
    signals: list[AlertSignal] = []

    if (
        metrics.temperature is not None
        and metrics.temperature >= config.temperature_critical
    ):
        signals.append(
            AlertSignal(
                key="temperature-critical",
                summary=f"CPU temperature {metrics.temperature:.1f} C",
                spoken=(
                    "Die Prozessortemperatur liegt bei "
                    f"{metrics.temperature:.0f} Grad Celsius."
                ),
            )
        )

    if metrics.ram_percent >= config.ram_critical:
        signals.append(
            AlertSignal(
                key="ram-critical",
                summary=f"RAM {metrics.ram_percent:.1f}%",
                spoken=(
                    "Die Arbeitsspeicherauslastung liegt bei "
                    f"{metrics.ram_percent:.0f} Prozent."
                ),
            )
        )

    if metrics.disk_percent >= config.disk_critical:
        signals.append(
            AlertSignal(
                key="disk-critical",
                summary=f"Disk {metrics.disk_percent:.1f}%",
                spoken=(
                    "Der Systemspeicher ist zu "
                    f"{metrics.disk_percent:.0f} Prozent belegt."
                ),
            )
        )

    current_throttle_flags = int(metrics.throttled_flags) & 0xF
    if current_throttle_flags:
        details: list[str] = []
        if current_throttle_flags & 0x1:
            details.append("Unterspannung")
        if current_throttle_flags & 0x2:
            details.append("Frequenzbegrenzung")
        if current_throttle_flags & 0x4:
            details.append("CPU Drosselung")
        if current_throttle_flags & 0x8:
            details.append("Temperaturlimit")
        label = ", ".join(details) or "Hardware Drosselung"
        signals.append(
            AlertSignal(
                key="hardware-throttling",
                summary=label,
                spoken=f"Der Raspberry Pi meldet aktuell {label}.",
            )
        )

    for result in service_results:
        if result.status != "offline":
            continue
        safe_name = result.name.replace(".service", "")
        signals.append(
            AlertSignal(
                key=f"service-offline:{result.name}",
                summary=f"Service offline: {safe_name}",
                spoken=f"Der Dienst {safe_name} ist nicht erreichbar.",
            )
        )

    return signals


class AlertStateStore:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.path = directory / "state.json"
        self.last_alerted: dict[str, float] = {}
        self.last_recovery: dict[str, float] = {}
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError, TypeError):
            logger.warning("Ignoring invalid HomePi alert state file", exc_info=True)
            return
        if not isinstance(raw, dict):
            return
        for field_name, target in (
            ("last_alerted", self.last_alerted),
            ("last_recovery", self.last_recovery),
        ):
            values = raw.get(field_name, {})
            if not isinstance(values, dict):
                continue
            for key, value in values.items():
                try:
                    target[str(key)] = float(value)
                except (TypeError, ValueError):
                    continue

    def save(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 2,
            "last_alerted": self.last_alerted,
            "last_recovery": self.last_recovery,
        }
        fd, temp_name = tempfile.mkstemp(
            prefix=".state-",
            suffix=".json",
            dir=self.directory,
            text=True,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
            os.chmod(self.path, 0o600)
        finally:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass


class AlertGate:
    def __init__(
        self,
        *,
        confirm_seconds: int,
        min_alert_interval_seconds: int,
        state: AlertStateStore,
    ) -> None:
        self.confirm_seconds = max(0, int(confirm_seconds))
        self.min_alert_interval_seconds = max(1, int(min_alert_interval_seconds))
        self.state = state
        self.first_seen: dict[str, float] = {}

    def due(
        self,
        signals: Sequence[AlertSignal],
        *,
        now: float | None = None,
    ) -> list[AlertSignal]:
        current = time.time() if now is None else float(now)
        present = {signal.key for signal in signals}

        for key in tuple(self.first_seen):
            if key not in present:
                del self.first_seen[key]

        due: list[AlertSignal] = []
        for signal in signals:
            first = self.first_seen.setdefault(signal.key, current)
            if current - first < self.confirm_seconds:
                continue
            last = self.state.last_alerted.get(signal.key)
            if (
                last is not None
                and current - last < self.min_alert_interval_seconds
            ):
                continue
            due.append(signal)
        return due

    def mark_alerted(
        self,
        signals: Sequence[AlertSignal],
        *,
        now: float | None = None,
    ) -> None:
        current = time.time() if now is None else float(now)
        for signal in signals:
            self.state.last_alerted[signal.key] = current
        self.state.save()


def _greeting(at: datetime) -> str:
    hour = at.hour
    if 5 <= hour < 11:
        return "Guten Morgen"
    if 11 <= hour < 18:
        return "Guten Tag"
    return "Guten Abend"


def compose_voice_message(
    signals: Sequence[AlertSignal],
    *,
    at: datetime | None = None,
) -> str:
    if not signals:
        raise ValueError("At least one alert signal is required")

    current = at or datetime.now().astimezone()
    selected = list(signals[:3])
    details = " ".join(signal.spoken for signal in selected)
    extra = len(signals) - len(selected)
    if extra > 0:
        details += f" Zusätzlich liegen {extra} weitere kritische Meldungen vor."

    return (
        f"{_greeting(current)}. "
        "Ich muss Sie auf eine kritische Abweichung im HomePi System hinweisen. "
        f"{details} "
        "Ich überwache die Situation weiter."
    )


def compose_test_message(*, at: datetime | None = None) -> str:
    current = at or datetime.now().astimezone()
    return (
        f"{_greeting(current)}. "
        "Dies ist ein Testanruf des HomePi Assistenzsystems. "
        "Telefonie, Sprachausgabe und die Alarmkette arbeiten bis zu diesem Punkt korrekt."
    )


class LocalSpeechRenderer:
    def __init__(self, config: PhoneAlertConfig) -> None:
        self.config = config

    def _require_binary(self, binary: str) -> str:
        path = shutil.which(binary)
        if path is None:
            raise RuntimeError(
                f"{binary} is not installed. Run the HomePi phone-alert installer."
            )
        return path

    def render(self, text: str, *, prefix: str = "alert") -> Path:
        espeak = self._require_binary("espeak-ng")
        sox = self._require_binary("sox")
        self.config.audio_dir.mkdir(parents=True, exist_ok=True)

        safe_prefix = re.sub(r"[^A-Za-z0-9_.-]", "-", prefix)[:32] or "audio"
        token = f"{int(time.time())}-{time.monotonic_ns()}"
        source = self.config.audio_dir / f"{safe_prefix}-{token}.source.wav"
        output = self.config.audio_dir / f"{safe_prefix}-{token}.wav"

        try:
            subprocess.run(
                [
                    espeak,
                    "-v",
                    self.config.voice,
                    "-s",
                    str(self.config.voice_speed),
                    "-p",
                    str(self.config.voice_pitch),
                    "-a",
                    str(self.config.voice_amplitude),
                    "-w",
                    str(source),
                    text,
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            subprocess.run(
                [
                    sox,
                    str(source),
                    "-r",
                    "8000",
                    "-c",
                    "1",
                    "-e",
                    "signed-integer",
                    "-b",
                    "16",
                    str(output),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            os.chmod(output, 0o640)
            try:
                asterisk_gid = grp.getgrnam("asterisk").gr_gid
                os.chown(output, -1, asterisk_gid)
            except (KeyError, PermissionError):
                logger.debug(
                    "Could not set Asterisk group on rendered audio; relying on directory permissions"
                )
            self._prune_old_audio()
            return output
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or "").strip()
            raise RuntimeError(
                f"Local speech rendering failed: {stderr[-300:] or exc}"
            ) from exc
        finally:
            try:
                source.unlink()
            except FileNotFoundError:
                pass

    def _prune_old_audio(self, *, max_age_seconds: int = 86400) -> None:
        cutoff = time.time() - max_age_seconds
        try:
            files = list(self.config.audio_dir.glob("*.wav"))
        except OSError:
            return
        for path in files:
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                continue


def build_status_snapshot(
    metrics: SystemMetrics,
    service_results: Sequence[HealthResult],
    signals: Sequence[AlertSignal],
    config: PhoneAlertConfig,
) -> dict[str, object]:
    services = [
        {
            "name": result.name,
            "status": result.status,
            "message": result.message,
        }
        for result in service_results
    ]
    recoverable = [
        result.name
        for result in service_results
        if (
            result.status == "offline"
            and result.name in SAFE_RECOVERY_UNITS
        )
    ]
    return {
        "version": 1,
        "generated_at": time.time(),
        "metrics": {
            "temperature": metrics.temperature,
            "ram_percent": metrics.ram_percent,
            "disk_percent": metrics.disk_percent,
            "cpu_percent": metrics.cpu_average_30s,
            "throttled_flags": int(metrics.throttled_flags),
        },
        "services": services,
        "active_alerts": [
            {
                "key": signal.key,
                "summary": signal.summary,
                "severity": signal.severity,
            }
            for signal in signals
        ],
        "recovery": {
            "enabled": config.allow_recovery,
            "recoverable_units": recoverable,
        },
    }


def write_json_atomic(
    path: Path,
    payload: dict[str, object],
    *,
    mode: int = 0o640,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, mode)
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def build_call_file_text(config: PhoneAlertConfig, audio_file: Path) -> str:
    config.validate_for_calling()
    resolved = audio_file.resolve()
    if resolved.suffix.lower() != ".wav":
        raise ValueError("Asterisk playback audio must be a .wav file")

    playback_path = str(resolved.with_suffix(""))
    channel = f"PJSIP/{config.target}@{config.pjsip_trunk}"
    common = [
        f"Channel: {channel}",
        f"MaxRetries: {config.call_max_retries}",
        f"RetryTime: {config.call_retry_seconds}",
        f"WaitTime: {config.call_wait_seconds}",
    ]

    if config.interactive:
        common.extend(
            [
                f"Context: {config.dialplan_context}",
                "Extension: s",
                "Priority: 1",
                f"Setvar: HOMEPI_ALERT_AUDIO={playback_path}",
                f"Setvar: HOMEPI_ALERT_STATUS_PATH={config.status_path}",
                f"Setvar: HOMEPI_ALERT_REQUEST_DIR={config.request_dir}",
                f"Setvar: HOMEPI_ALERT_RESULT_DIR={config.result_dir}",
                (
                    "Setvar: HOMEPI_ALERT_ALLOW_RECOVERY="
                    f"{'1' if config.allow_recovery else '0'}"
                ),
            ]
        )
    else:
        common.extend(
            [
                "Application: Playback",
                f"Data: {playback_path}",
            ]
        )

    common.append("")
    return "\n".join(common)


class AsteriskCallFileDialer:
    def __init__(self, config: PhoneAlertConfig) -> None:
        self.config = config

    def queue(self, audio_file: Path) -> Path:
        body = build_call_file_text(self.config, audio_file)

        if not self.config.staging_dir.is_dir():
            raise RuntimeError(
                f"Asterisk staging directory missing: {self.config.staging_dir}"
            )
        if not self.config.outgoing_dir.is_dir():
            raise RuntimeError(
                f"Asterisk outgoing directory missing: {self.config.outgoing_dir}"
            )
        if not os.access(self.config.staging_dir, os.W_OK):
            raise RuntimeError(
                f"Asterisk staging directory is not writable: {self.config.staging_dir}"
            )
        if not os.access(self.config.outgoing_dir, os.W_OK):
            raise RuntimeError(
                f"Asterisk outgoing directory is not writable: {self.config.outgoing_dir}"
            )

        stamp = f"{int(time.time())}-{time.monotonic_ns()}"
        staging_path = self.config.staging_dir / f"homepi-{stamp}.call"
        outgoing_path = self.config.outgoing_dir / staging_path.name

        fd = os.open(
            staging_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o640,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(body)
                handle.flush()
                os.fsync(handle.fileno())

            try:
                asterisk_gid = grp.getgrnam("asterisk").gr_gid
                os.chown(staging_path, -1, asterisk_gid)
            except (KeyError, PermissionError):
                logger.debug(
                    "Could not force Asterisk group on call file; relying on setgid spool directory"
                )

            os.chmod(staging_path, 0o640)
            os.replace(staging_path, outgoing_path)
            logger.info("Queued Asterisk alert call as %s", outgoing_path.name)
            return outgoing_path
        finally:
            try:
                staging_path.unlink()
            except FileNotFoundError:
                pass
