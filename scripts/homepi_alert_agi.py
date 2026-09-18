#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

_INCIDENT_ID_RE = re.compile(r"^[a-f0-9]{32}$")
_UNIT_RE = re.compile(r"^[A-Za-z0-9_.@:-]{1,128}$")


class AGI:
    def __init__(self) -> None:
        self.environment: dict[str, str] = {}
        while True:
            line = sys.stdin.readline()
            if not line:
                break
            line = line.rstrip("\r\n")
            if not line:
                break
            key, _, value = line.partition(":")
            self.environment[key.strip()] = value.strip()

    def command(self, value: str) -> str:
        sys.stdout.write(value + "\n")
        sys.stdout.flush()
        response = sys.stdin.readline().rstrip("\r\n")
        return response

    @staticmethod
    def _result(response: str) -> str:
        marker = "result="
        pos = response.find(marker)
        if pos < 0:
            return ""
        value = response[pos + len(marker):].split(" ", 1)[0]
        return value.strip()

    def answer(self) -> None:
        self.command("ANSWER")

    def stream(self, audio_file: Path) -> None:
        path = str(audio_file.resolve().with_suffix(""))
        self.command(f'STREAM FILE "{path}" ""')

    def get_digits(
        self,
        audio_file: Path,
        timeout_ms: int = 9000,
        max_digits: int = 1,
    ) -> str:
        path = str(audio_file.resolve().with_suffix(""))
        digits = max(1, min(16, int(max_digits)))
        response = self.command(
            f'GET DATA "{path}" {int(timeout_ms)} {digits}'
        )
        result = self._result(response)
        return result if result.isdigit() else ""

    def get_digit(self, audio_file: Path, timeout_ms: int = 9000) -> str:
        return self.get_digits(audio_file, timeout_ms, 1)

    def hangup(self) -> None:
        self.command("HANGUP")


def _load_incident(path: Path) -> dict[str, object]:
    if path.suffix != ".json" or not _INCIDENT_ID_RE.fullmatch(path.stem):
        raise ValueError("Invalid incident path")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or str(raw.get("id", "")) != path.stem:
        raise ValueError("Incident payload does not match path")
    return raw


def _require_binary(name: str) -> str:
    binary = shutil.which(name)
    if binary is None:
        raise RuntimeError(f"{name} is not installed")
    return binary


def _shared_child(root: Path, name: str) -> Path:
    path = (root / name).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError("Invalid HomePi shared spool path") from exc
    path.mkdir(parents=True, exist_ok=True)
    return path


def _render_speech(text: str, audio_dir: Path, prefix: str) -> Path:
    espeak = _require_binary("espeak-ng")
    sox = _require_binary("sox")
    token = f"{prefix}-{uuid.uuid4().hex}"
    source = audio_dir / f"{token}.source.wav"
    output = audio_dir / f"{token}.wav"

    try:
        subprocess.run(
            [
                espeak,
                "-v",
                "de",
                "-s",
                "145",
                "-p",
                "38",
                "-a",
                "135",
                "-w",
                str(source),
                text,
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=25,
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
            timeout=25,
        )
        os.chmod(output, 0o660)
        return output
    finally:
        try:
            source.unlink()
        except FileNotFoundError:
            pass


def _temperature() -> float | None:
    for path in (
        Path("/sys/class/thermal/thermal_zone0/temp"),
        Path("/sys/class/hwmon/hwmon0/temp1_input"),
    ):
        try:
            value = float(path.read_text(encoding="utf-8").strip())
            if value > 1000:
                value /= 1000.0
            if -20 <= value <= 130:
                return value
        except (OSError, ValueError):
            continue
    return None


def _memory_percent() -> float | None:
    values: dict[str, int] = {}
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            key, _, rest = line.partition(":")
            amount = rest.strip().split(" ", 1)[0]
            if amount.isdigit():
                values[key] = int(amount)
    except OSError:
        return None

    total = values.get("MemTotal", 0)
    available = values.get("MemAvailable", 0)
    if total <= 0:
        return None
    return max(0.0, min(100.0, (total - available) * 100.0 / total))


def _service_status(unit: str | None) -> str | None:
    if not unit or not _UNIT_RE.fullmatch(unit):
        return None
    try:
        result = subprocess.run(
            ["systemctl", "is-active", unit],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return (result.stdout or result.stderr or "unbekannt").strip()


def _system_status_text(incident: dict[str, object]) -> str:
    pieces: list[str] = ["Systemstatus."]
    temp = _temperature()
    ram = _memory_percent()
    disk = shutil.disk_usage("/")
    disk_percent = (disk.used / disk.total * 100.0) if disk.total else 0.0

    if temp is not None:
        pieces.append(f"Prozessortemperatur {temp:.0f} Grad.")
    if ram is not None:
        pieces.append(f"Arbeitsspeicher {ram:.0f} Prozent.")
    pieces.append(f"Systemspeicher {disk_percent:.0f} Prozent belegt.")

    unit = str(incident.get("restart_unit") or "")
    status = _service_status(unit)
    if unit and status:
        spoken_unit = unit.removesuffix(".service").replace("-", " ")
        pieces.append(f"Dienst {spoken_unit}: {status}.")
    return " ".join(pieces)


def _atomic_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.stem}-",
        suffix=".tmp",
        dir=path.parent,
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, 0o660)
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def _acknowledge(incident: dict[str, object], ack_dir: Path) -> None:
    incident_id = str(incident["id"])
    path = ack_dir / f"{incident_id}.ack"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT, 0o660)
    os.close(fd)


def _request_restart(
    incident: dict[str, object],
    action_dir: Path,
) -> tuple[bool, str]:
    unit = str(incident.get("restart_unit") or "")
    if not unit or not _UNIT_RE.fullmatch(unit):
        return False, "Für diesen Vorfall ist kein sicherer Neustart verfügbar."

    request_id = uuid.uuid4().hex
    request_path = action_dir / f"{request_id}.request.json"
    result_path = action_dir / f"{request_id}.result.json"
    _atomic_json(
        request_path,
        {
            "version": 1,
            "request_id": request_id,
            "incident_id": str(incident["id"]),
            "action": "restart",
            "unit": unit,
            "created_at": time.time(),
        },
    )

    timeout = max(5, min(120, int(incident.get("action_timeout_seconds", 30))))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            raw = json.loads(result_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            time.sleep(0.4)
            continue
        except (OSError, ValueError, TypeError):
            return False, "Die Rückmeldung des Neustarts war ungültig."

        try:
            result_path.unlink()
        except OSError:
            pass
        ok = bool(raw.get("ok"))
        status = str(raw.get("status") or "")
        spoken = unit.removesuffix(".service").replace("-", " ")
        if status == "already-online":
            return True, (
                f"Der Dienst {spoken} ist bereits wieder erreichbar. "
                "Ein Neustart war nicht mehr erforderlich."
            )
        if status == "recovered" and ok:
            return True, (
                f"Verstanden. Der Dienst {spoken} wurde neu gestartet "
                "und ist wieder aktiv."
            )
        if status == "expired":
            return False, (
                "Die Sicherheitsanfrage war nicht mehr aktuell und wurde verworfen."
            )
        if status == "restart-unverified":
            return False, (
                f"Der Neustart von {spoken} wurde ausgeführt, "
                "aber der aktive Zustand konnte nicht bestätigt werden."
            )
        return False, f"Der Neustart von {spoken} war nicht erfolgreich."

    return False, "Der Neustart wurde angefordert, aber noch nicht bestätigt."


def _cleanup_generated(audio_dir: Path) -> None:
    cutoff = time.time() - 3600
    for path in audio_dir.glob("agi-*.wav"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            continue


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        return 2

    incident_path = Path(argv[1]).resolve()
    try:
        incident = _load_incident(incident_path)
        shared_root = incident_path.parent.parent.resolve()
        audio_dir = _shared_child(shared_root, "audio")
        action_dir = _shared_child(shared_root, "actions")
        ack_dir = _shared_child(shared_root, "ack")
        alert_audio = Path(str(incident["audio_file"])).resolve()
        try:
            alert_audio.relative_to(audio_dir)
        except ValueError as exc:
            raise ValueError("Incident audio is outside HomePi audio spool") from exc
        if not alert_audio.is_file():
            raise FileNotFoundError(alert_audio)
    except Exception:
        return 3

    agi = AGI()
    agi.answer()
    _cleanup_generated(audio_dir)

    intro = _render_speech(
        "HomePi Assistent. Eingehende Systemmeldung.",
        audio_dir,
        "agi-intro",
    )
    menu = _render_speech(
        "Drücken Sie 1 zum Bestätigen. "
        "2 für den aktuellen Systemstatus. "
        "3 für einen sicheren Neustart des betroffenen Dienstes, falls verfügbar. "
        "9 zum Wiederholen. 0 zum Beenden.",
        audio_dir,
        "agi-menu",
    )
    confirmed = _render_speech(
        "Bestätigt. Ich überwache den Vorfall weiter und eskaliere diesen Anruf nicht erneut.",
        audio_dir,
        "agi-confirmed",
    )
    goodbye = _render_speech(
        "Verstanden. Ich beende die Verbindung.",
        audio_dir,
        "agi-goodbye",
    )
    timeout_audio = _render_speech(
        "Keine Eingabe erkannt. Der Vorfall bleibt unbestätigt. Ich beende die Verbindung.",
        audio_dir,
        "agi-timeout",
    )
    pin_prompt = _render_speech(
        "Bitte geben Sie jetzt die Sicherheits PIN ein.",
        audio_dir,
        "agi-pin",
    )
    pin_rejected = _render_speech(
        "Die Sicherheits PIN ist nicht korrekt. Die Aktion wurde nicht ausgeführt.",
        audio_dir,
        "agi-pin-rejected",
    )
    pin_missing = _render_speech(
        "Für Systemaktionen ist keine Sicherheits PIN eingerichtet. Der Neustart bleibt gesperrt.",
        audio_dir,
        "agi-pin-missing",
    )

    agi.stream(intro)
    agi.stream(alert_audio)

    for _ in range(5):
        digit = agi.get_digit(menu)
        if digit == "1":
            _acknowledge(incident, ack_dir)
            agi.stream(confirmed)
            agi.hangup()
            return 0
        if digit == "2":
            status_audio = _render_speech(
                _system_status_text(incident),
                audio_dir,
                "agi-status",
            )
            agi.stream(status_audio)
            continue
        if digit == "3":
            expected_hash = str(incident.get("action_pin_hash") or "")
            if not expected_hash:
                agi.stream(pin_missing)
                continue
            entered_pin = agi.get_digits(
                pin_prompt,
                timeout_ms=12000,
                max_digits=8,
            )
            entered_hash = hashlib.sha256(
                entered_pin.encode("utf-8")
            ).hexdigest()
            if not entered_pin or not hmac.compare_digest(
                entered_hash,
                expected_hash,
            ):
                agi.stream(pin_rejected)
                continue

            _, message = _request_restart(incident, action_dir)
            result_audio = _render_speech(
                message,
                audio_dir,
                "agi-action",
            )
            agi.stream(result_audio)
            continue
        if digit == "9":
            agi.stream(alert_audio)
            continue
        if digit == "0":
            agi.stream(goodbye)
            agi.hangup()
            return 0
        if not digit:
            agi.stream(timeout_audio)
            agi.hangup()
            return 0

        invalid = _render_speech(
            "Diese Auswahl ist nicht verfügbar.",
            audio_dir,
            "agi-invalid",
        )
        agi.stream(invalid)

    agi.stream(goodbye)
    agi.hangup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
