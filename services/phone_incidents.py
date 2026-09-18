from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Sequence

from services.phone_alerts import AlertSignal, PhoneAlertConfig
from services.process_runner import ProcessResult, run_process

logger = logging.getLogger(__name__)

_INCIDENT_ID_RE = re.compile(r"^[a-f0-9]{32}$")
_UNIT_RE = re.compile(r"^[A-Za-z0-9_.@:-]{1,128}$")
HELPER = "/usr/local/sbin/homepi-systemctl"


def _env_bool(name: str, default: bool) -> bool:
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


@dataclass(frozen=True, slots=True)
class InteractiveAlertConfig:
    enabled: bool
    root_dir: Path
    agi_script: Path
    escalation_seconds: int
    max_escalations: int
    action_timeout_seconds: int
    restartable_services: tuple[str, ...]

    @classmethod
    def from_env(cls) -> "InteractiveAlertConfig":
        root = Path(
            os.getenv(
                "HOMEPI_ALERT_SHARED_DIR",
                "/var/spool/asterisk/homepi-alerts",
            )
        )
        raw_services = os.getenv(
            "HOMEPI_ALERT_RESTARTABLE_SERVICES",
            "raspberry-bot.service,raspberry-dashboard.service,pihole-FTL.service",
        )
        services = tuple(
            unit.strip()
            for unit in raw_services.split(",")
            if unit.strip()
        )
        for unit in services:
            if not _UNIT_RE.fullmatch(unit):
                raise ValueError(
                    f"HOMEPI_ALERT_RESTARTABLE_SERVICES contains invalid unit: {unit}"
                )
        return cls(
            enabled=_env_bool("HOMEPI_ALERT_INTERACTIVE", True),
            root_dir=root,
            agi_script=Path(
                os.getenv(
                    "HOMEPI_ALERT_AGI_SCRIPT",
                    "/usr/local/lib/homepi-alert-agi.py",
                )
            ),
            escalation_seconds=_env_int(
                "HOMEPI_ALERT_ESCALATION_SECONDS", 300, 60, 86400
            ),
            max_escalations=_env_int(
                "HOMEPI_ALERT_MAX_ESCALATIONS", 2, 0, 10
            ),
            action_timeout_seconds=_env_int(
                "HOMEPI_ALERT_ACTION_TIMEOUT_SECONDS", 30, 5, 120
            ),
            restartable_services=services,
        )

    @property
    def incidents_dir(self) -> Path:
        return self.root_dir / "incidents"

    @property
    def actions_dir(self) -> Path:
        return self.root_dir / "actions"

    @property
    def acknowledgements_dir(self) -> Path:
        return self.root_dir / "ack"

    @property
    def audio_dir(self) -> Path:
        return self.root_dir / "audio"

    @property
    def call_staging_dir(self) -> Path:
        return self.root_dir / "call-staging"


@dataclass(slots=True)
class Incident:
    id: str
    created_at: float
    updated_at: float
    signal_keys: list[str]
    summaries: list[str]
    audio_file: str
    restart_unit: str | None
    attempts: int
    last_queued_at: float
    acknowledged: bool
    closed: bool
    test: bool = False

    def as_dict(self, config: InteractiveAlertConfig) -> dict[str, object]:
        payload = asdict(self)
        payload.update(
            {
                "version": 1,
                "action_dir": str(config.actions_dir),
                "ack_dir": str(config.acknowledgements_dir),
                "audio_dir": str(config.audio_dir),
                "action_timeout_seconds": config.action_timeout_seconds,
            }
        )
        return payload


class IncidentStore:
    def __init__(self, config: InteractiveAlertConfig) -> None:
        self.config = config

    def ensure_directories(self) -> None:
        for path in (
            self.config.root_dir,
            self.config.incidents_dir,
            self.config.actions_dir,
            self.config.acknowledgements_dir,
            self.config.audio_dir,
            self.config.call_staging_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def _path(self, incident_id: str) -> Path:
        if not _INCIDENT_ID_RE.fullmatch(incident_id):
            raise ValueError("Invalid incident id")
        return self.config.incidents_dir / f"{incident_id}.json"

    def _atomic_json(self, path: Path, payload: dict[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{path.stem}-",
            suffix=".tmp",
            dir=path.parent,
            text=True,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_name, 0o660)
            os.replace(temp_name, path)
        finally:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass

    def create(
        self,
        signals: Sequence[AlertSignal],
        *,
        audio_file: Path,
        restart_unit: str | None,
        test: bool = False,
        now: float | None = None,
    ) -> Incident:
        self.ensure_directories()
        current = time.time() if now is None else float(now)
        if restart_unit and restart_unit not in self.config.restartable_services:
            restart_unit = None
        incident = Incident(
            id=uuid.uuid4().hex,
            created_at=current,
            updated_at=current,
            signal_keys=[signal.key for signal in signals],
            summaries=[signal.summary for signal in signals],
            audio_file=str(audio_file.resolve()),
            restart_unit=restart_unit,
            attempts=0,
            last_queued_at=0.0,
            acknowledged=False,
            closed=False,
            test=test,
        )
        self.save(incident)
        return incident

    def save(self, incident: Incident) -> None:
        incident.updated_at = time.time()
        self._atomic_json(self._path(incident.id), incident.as_dict(self.config))

    def load(self, incident_id: str) -> Incident | None:
        path = self._path(incident_id)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError, TypeError):
            logger.warning("Invalid incident file ignored: %s", path, exc_info=True)
            return None
        try:
            return Incident(
                id=str(raw["id"]),
                created_at=float(raw["created_at"]),
                updated_at=float(raw["updated_at"]),
                signal_keys=[str(x) for x in raw.get("signal_keys", [])],
                summaries=[str(x) for x in raw.get("summaries", [])],
                audio_file=str(raw["audio_file"]),
                restart_unit=(
                    str(raw["restart_unit"])
                    if raw.get("restart_unit")
                    else None
                ),
                attempts=int(raw.get("attempts", 0)),
                last_queued_at=float(raw.get("last_queued_at", 0.0)),
                acknowledged=bool(raw.get("acknowledged", False)),
                closed=bool(raw.get("closed", False)),
                test=bool(raw.get("test", False)),
            )
        except (KeyError, TypeError, ValueError):
            logger.warning("Malformed incident payload ignored: %s", path)
            return None

    def active(self) -> list[Incident]:
        self.ensure_directories()
        incidents: list[Incident] = []
        for path in sorted(self.config.incidents_dir.glob("*.json")):
            incident = self.load(path.stem)
            if incident is not None and not incident.closed:
                incidents.append(incident)
        return incidents

    def consume_acknowledgements(self) -> int:
        self.ensure_directories()
        count = 0
        for path in self.config.acknowledgements_dir.glob("*.ack"):
            incident_id = path.stem
            if not _INCIDENT_ID_RE.fullmatch(incident_id):
                try:
                    path.unlink()
                except OSError:
                    pass
                continue
            incident = self.load(incident_id)
            if incident is not None:
                incident.acknowledged = True
                incident.closed = True
                self.save(incident)
                count += 1
            try:
                path.unlink()
            except OSError:
                pass
        return count

    def reconcile(
        self,
        current_signal_keys: set[str],
        *,
        now: float | None = None,
    ) -> list[Incident]:
        current = time.time() if now is None else float(now)
        due: list[Incident] = []
        maximum_attempts = 1 + self.config.max_escalations

        for incident in self.active():
            if incident.test:
                continue
            if incident.acknowledged:
                incident.closed = True
                self.save(incident)
                continue
            if not set(incident.signal_keys).intersection(current_signal_keys):
                incident.closed = True
                self.save(incident)
                continue
            if incident.attempts >= maximum_attempts:
                continue
            if incident.last_queued_at <= 0:
                continue
            if current - incident.last_queued_at >= self.config.escalation_seconds:
                due.append(incident)
        return due


def build_interactive_call_file(
    phone: PhoneAlertConfig,
    interactive: InteractiveAlertConfig,
    incident: Incident,
) -> str:
    phone.validate_for_calling()
    if not _INCIDENT_ID_RE.fullmatch(incident.id):
        raise ValueError("Invalid incident id")
    incident_path = interactive.incidents_dir / f"{incident.id}.json"
    agi = interactive.agi_script
    for value, label in ((str(agi), "AGI path"), (str(incident_path), "incident path")):
        if "\n" in value or "\r" in value or "," in value:
            raise ValueError(f"Unsafe {label}")
    channel = f"PJSIP/{phone.target}@{phone.pjsip_trunk}"
    return "\n".join(
        [
            f"Channel: {channel}",
            f"MaxRetries: {phone.call_max_retries}",
            f"RetryTime: {phone.call_retry_seconds}",
            f"WaitTime: {phone.call_wait_seconds}",
            "Application: AGI",
            f"Data: {agi},{incident_path}",
            "",
        ]
    )


class InteractiveAsteriskDialer:
    def __init__(
        self,
        phone: PhoneAlertConfig,
        interactive: InteractiveAlertConfig,
        store: IncidentStore,
    ) -> None:
        self.phone = phone
        self.interactive = interactive
        self.store = store

    def queue(self, incident: Incident) -> Path:
        self.store.ensure_directories()
        if not self.interactive.agi_script.is_file():
            raise RuntimeError(
                f"Interactive AGI script missing: {self.interactive.agi_script}"
            )
        if not self.phone.outgoing_dir.is_dir():
            raise RuntimeError(
                f"Asterisk outgoing directory missing: {self.phone.outgoing_dir}"
            )
        if not os.access(self.phone.outgoing_dir, os.W_OK):
            raise RuntimeError(
                f"Asterisk outgoing directory is not writable: {self.phone.outgoing_dir}"
            )

        body = build_interactive_call_file(
            self.phone,
            self.interactive,
            incident,
        )
        token = f"{incident.id}-{time.monotonic_ns()}"
        staging_path = self.interactive.call_staging_dir / f"homepi-{token}.call"
        outgoing_path = self.phone.outgoing_dir / staging_path.name

        fd = os.open(
            staging_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o660,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(body)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(staging_path, outgoing_path)
        finally:
            try:
                staging_path.unlink()
            except FileNotFoundError:
                pass

        incident.attempts += 1
        incident.last_queued_at = time.time()
        self.store.save(incident)
        logger.warning(
            "Queued interactive HomePi call for incident %s (attempt %d)",
            incident.id,
            incident.attempts,
        )
        return outgoing_path


def _load_action(path: Path) -> dict[str, object] | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(raw, dict):
        return None
    return raw


async def process_action_requests(
    config: InteractiveAlertConfig,
) -> int:
    config.actions_dir.mkdir(parents=True, exist_ok=True)
    processed = 0
    for request_path in sorted(config.actions_dir.glob("*.request.json")):
        raw = _load_action(request_path)
        request_id = request_path.name.removesuffix(".request.json")
        result_path = config.actions_dir / f"{request_id}.result.json"

        ok = False
        detail = "Invalid request"
        unit = ""
        if raw is not None:
            action = str(raw.get("action", ""))
            unit = str(raw.get("unit", ""))
            if (
                action == "restart"
                and unit in config.restartable_services
                and _UNIT_RE.fullmatch(unit)
            ):
                result: ProcessResult = await run_process(
                    ["sudo", "-n", HELPER, "restart", unit],
                    timeout=float(config.action_timeout_seconds),
                )
                ok = result.ok
                detail = (
                    result.stdout
                    or result.stderr
                    or ("Restart successful" if ok else "Restart failed")
                )[-700:]
            else:
                detail = "Action or service is not allowlisted"

        payload = {
            "version": 1,
            "request_id": request_id,
            "ok": ok,
            "unit": unit,
            "detail": detail,
            "completed_at": time.time(),
        }
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{request_id}-",
            suffix=".tmp",
            dir=config.actions_dir,
            text=True,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_name, 0o660)
            os.replace(temp_name, result_path)
        finally:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass

        try:
            request_path.unlink()
        except OSError:
            logger.warning("Could not delete processed action request %s", request_path)
        processed += 1
    return processed


def restart_unit_for_signals(
    signals: Iterable[AlertSignal],
    restartable_services: Sequence[str],
) -> str | None:
    allowed = set(restartable_services)
    units: list[str] = []
    for signal in signals:
        if not signal.key.startswith("service-offline:"):
            continue
        unit = signal.key.removeprefix("service-offline:")
        if not unit.endswith(".service"):
            unit = f"{unit}.service"
        if unit in allowed:
            units.append(unit)
    unique = sorted(set(units))
    return unique[0] if len(unique) == 1 else None
