#!/usr/bin/env python3
from __future__ import annotations

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

_RESULT_RE = re.compile(r"^200 result=(-?\d+)(?: \((.*)\))?")
_SAFE_PATH_RE = re.compile(r"^/[A-Za-z0-9_./-]+$")
_SAFE_VOICE_RE = re.compile(r"^[A-Za-z0-9_+.-]{1,32}$")

SERVICE_LABELS = {
    "homepi-flight-radar": "Flight Radar",
    "pihole-FTL": "Pi-hole",
    "raspberry-bot": "Discord Bot",
    "raspberry-dashboard": "Dashboard",
    "raspberry-display": "Display eins",
    "raspberry-display2": "Display zwei",
    "raspberry-intelligence": "Intelligence",
    "raspberry-meshtastic": "Meshtastic",
}


class AgiError(RuntimeError):
    pass


def _read_agi_environment() -> dict[str, str]:
    values: dict[str, str] = {}
    while True:
        line = sys.stdin.readline()
        if line == "":
            break
        line = line.rstrip("\r\n")
        if not line:
            break
        key, sep, value = line.partition(":")
        if sep:
            values[key.strip()] = value.strip()
    return values


def _command(command: str) -> tuple[int, str]:
    print(command, flush=True)
    line = sys.stdin.readline()
    if not line:
        raise AgiError("Asterisk closed the AGI channel")
    line = line.rstrip("\r\n")
    match = _RESULT_RE.match(line)
    if not match:
        raise AgiError(f"Unexpected AGI response: {line[:200]}")
    return int(match.group(1)), match.group(2) or ""


def _get_variable(name: str, default: str = "") -> str:
    if not re.fullmatch(r"[A-Z0-9_]{1,64}", name):
        return default
    result, value = _command(f"GET VARIABLE {name}")
    if result != 1:
        return default
    return value


def _validate_path(raw: str, *, must_be_absolute: bool = True) -> Path:
    value = raw.strip()
    if not value or not _SAFE_PATH_RE.fullmatch(value):
        raise AgiError("Unsafe file path received from dialplan")
    path = Path(value)
    if must_be_absolute and not path.is_absolute():
        raise AgiError("Expected absolute file path")
    return path


def _audio_base(path: Path) -> str:
    resolved = path.resolve()
    if resolved.suffix.lower() == ".wav":
        resolved = resolved.with_suffix("")
    value = str(resolved)
    if not _SAFE_PATH_RE.fullmatch(value):
        raise AgiError("Unsafe audio path")
    return value


def _play(path: Path) -> bool:
    result, _ = _command(f'STREAM FILE {_audio_base(path)} ""')
    return result >= 0


def _get_digit(prompt: Path, timeout_ms: int = 8000) -> str:
    result, _ = _command(
        f"GET DATA {_audio_base(prompt)} {max(1000, timeout_ms)} 1"
    )
    if result < 0:
        raise AgiError("Channel disconnected")
    return str(result) if result else ""


def _voice_settings() -> tuple[str, int, int, int]:
    voice = _get_variable("HOMEPI_ALERT_VOICE", "de")
    if not _SAFE_VOICE_RE.fullmatch(voice):
        voice = "de"

    def number(name: str, default: int, low: int, high: int) -> int:
        try:
            value = int(_get_variable(name, str(default)))
        except ValueError:
            return default
        return max(low, min(high, value))

    return (
        voice,
        number("HOMEPI_ALERT_VOICE_SPEED", 145, 80, 250),
        number("HOMEPI_ALERT_VOICE_PITCH", 38, 0, 99),
        number("HOMEPI_ALERT_VOICE_AMPLITUDE", 135, 0, 200),
    )


def _render(
    text: str,
    *,
    audio_dir: Path,
    prefix: str,
    created: list[Path],
) -> Path:
    espeak = shutil.which("espeak-ng")
    sox = shutil.which("sox")
    if espeak is None or sox is None:
        raise AgiError("Local speech tools are unavailable")

    voice, speed, pitch, amplitude = _voice_settings()
    audio_dir.mkdir(parents=True, exist_ok=True)
    token = f"{os.getpid()}-{time.monotonic_ns()}"
    safe_prefix = re.sub(r"[^A-Za-z0-9_.-]", "-", prefix)[:24] or "prompt"
    source = audio_dir / f"agi-{safe_prefix}-{token}.source.wav"
    output = audio_dir / f"agi-{safe_prefix}-{token}.wav"

    try:
        subprocess.run(
            [
                espeak,
                "-v",
                voice,
                "-s",
                str(speed),
                "-p",
                str(pitch),
                "-a",
                str(amplitude),
                "-w",
                str(source),
                text,
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
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
            timeout=20,
        )
        os.chmod(output, 0o640)
        created.append(output)
        return output
    except (OSError, subprocess.SubprocessError) as exc:
        raise AgiError(f"Could not render voice prompt: {type(exc).__name__}") from exc
    finally:
        try:
            source.unlink()
        except FileNotFoundError:
            pass


def _load_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def _status_text(snapshot: dict[str, object]) -> str:
    generated_at = snapshot.get("generated_at")
    try:
        age = max(time.time() - float(generated_at), 0.0)
    except (TypeError, ValueError):
        age = 9999.0
    if age > 180:
        return (
            "Der Systemstatus ist momentan nicht aktuell genug für eine sichere Aussage. "
            "Die Überwachung läuft weiter."
        )

    metrics = snapshot.get("metrics")
    if not isinstance(metrics, dict):
        metrics = {}

    parts: list[str] = ["Aktueller Systemstatus."]
    temperature = metrics.get("temperature")
    ram = metrics.get("ram_percent")
    disk = metrics.get("disk_percent")
    cpu = metrics.get("cpu_percent")

    try:
        if temperature is not None:
            parts.append(f"Temperatur {float(temperature):.0f} Grad.")
    except (TypeError, ValueError):
        pass
    try:
        if cpu is not None:
            parts.append(f"CPU Auslastung im Mittel {float(cpu):.0f} Prozent.")
    except (TypeError, ValueError):
        pass
    try:
        if ram is not None:
            parts.append(f"Arbeitsspeicher {float(ram):.0f} Prozent.")
    except (TypeError, ValueError):
        pass
    try:
        if disk is not None:
            parts.append(f"Speicherplatz {float(disk):.0f} Prozent belegt.")
    except (TypeError, ValueError):
        pass

    services = snapshot.get("services")
    offline: list[str] = []
    if isinstance(services, list):
        for item in services:
            if not isinstance(item, dict) or item.get("status") != "offline":
                continue
            raw_name = str(item.get("name") or "")
            offline.append(SERVICE_LABELS.get(raw_name, raw_name))

    if offline:
        parts.append("Offline sind " + ", ".join(offline[:4]) + ".")
        if len(offline) > 4:
            parts.append(f"Zusätzlich sind {len(offline) - 4} weitere Dienste offline.")
    else:
        parts.append("Die überwachten Dienste sind erreichbar.")

    return " ".join(parts)


def _atomic_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, 0o640)
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def _request_recovery(
    request_dir: Path,
    result_dir: Path,
) -> tuple[str, Path]:
    request_id = uuid.uuid4().hex
    payload = {
        "version": 1,
        "id": request_id,
        "action": "recover_offline_services",
        "created_at": time.time(),
    }
    _atomic_json(request_dir / f"{request_id}.json", payload)
    return request_id, result_dir / f"{request_id}.json"


def _recovery_result_text(result: dict[str, object]) -> str:
    if not result:
        return (
            "Die Wiederherstellung wurde angefordert. "
            "Eine abschließende Rückmeldung liegt noch nicht vor."
        )
    status = str(result.get("status") or "unknown")
    attempted = result.get("attempted")
    recovered = result.get("recovered")
    failed = result.get("failed")
    attempted_names = attempted if isinstance(attempted, list) else []
    recovered_names = recovered if isinstance(recovered, list) else []
    failed_names = failed if isinstance(failed, list) else []

    if status == "disabled":
        return "Die telefonische Wiederherstellung ist derzeit deaktiviert."
    if status == "nothing-to-do":
        return "Es gibt aktuell keinen freigegebenen Offline Dienst zum Neustarten."
    if status == "rate-limited":
        return "Die Wiederherstellung wurde vor kurzem bereits versucht. Ich überwache weiter."

    if attempted_names and not failed_names:
        labels = [
            SERVICE_LABELS.get(str(name), str(name))
            for name in recovered_names or attempted_names
        ]
        return (
            "Die Wiederherstellung wurde ausgeführt. "
            + ", ".join(labels[:4])
            + " ist wieder erreichbar."
        )
    if failed_names:
        labels = [SERVICE_LABELS.get(str(name), str(name)) for name in failed_names]
        return (
            "Die Wiederherstellung wurde ausgeführt, aber "
            + ", ".join(labels[:4])
            + " konnte nicht erfolgreich bestätigt werden."
        )
    return "Die Wiederherstellung wurde verarbeitet. Ich überwache die Situation weiter."


def _wait_for_result(path: Path, timeout_seconds: float = 25.0) -> dict[str, object]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if path.is_file():
            return _load_json(path)
        time.sleep(0.5)
    return {}


def main() -> int:
    _read_agi_environment()

    alert_audio_raw = _get_variable("HOMEPI_ALERT_AUDIO")
    status_path_raw = _get_variable("HOMEPI_ALERT_STATUS_PATH")
    request_dir_raw = _get_variable("HOMEPI_ALERT_REQUEST_DIR")
    result_dir_raw = _get_variable("HOMEPI_ALERT_RESULT_DIR")
    allow_recovery = _get_variable("HOMEPI_ALERT_ALLOW_RECOVERY", "0") == "1"

    alert_audio = _validate_path(alert_audio_raw)
    status_path = _validate_path(status_path_raw)
    request_dir = _validate_path(request_dir_raw)
    result_dir = _validate_path(result_dir_raw)
    audio_dir = alert_audio.parent

    created: list[Path] = []
    try:
        _command("ANSWER")
        _play(alert_audio)

        menu = _render(
            (
                "Sie können jetzt reagieren. "
                "Drücken Sie 1 für den aktuellen Systemstatus. "
                "Drücken Sie 2 für eine sichere Wiederherstellung betroffener Dienste. "
                "Drücken Sie 3, um die Warnung zu wiederholen. "
                "Oder drücken Sie 9 zum Bestätigen und Beenden."
            ),
            audio_dir=audio_dir,
            prefix="menu",
            created=created,
        )

        invalid = _render(
            "Diese Auswahl ist nicht verfügbar.",
            audio_dir=audio_dir,
            prefix="invalid",
            created=created,
        )

        for _ in range(5):
            digit = _get_digit(menu)
            if digit == "1":
                status_audio = _render(
                    _status_text(_load_json(status_path)),
                    audio_dir=audio_dir,
                    prefix="status",
                    created=created,
                )
                _play(status_audio)
                continue

            if digit == "2":
                if not allow_recovery:
                    disabled = _render(
                        (
                            "Die telefonische Wiederherstellung ist aus Sicherheitsgründen "
                            "noch nicht aktiviert."
                        ),
                        audio_dir=audio_dir,
                        prefix="disabled",
                        created=created,
                    )
                    _play(disabled)
                    continue

                recovery = _load_json(status_path).get("recovery")
                recoverable = (
                    recovery.get("recoverable_units")
                    if isinstance(recovery, dict)
                    else []
                )
                if not isinstance(recoverable, list) or not recoverable:
                    nothing = _render(
                        "Es gibt aktuell keinen freigegebenen Offline Dienst zum Neustarten.",
                        audio_dir=audio_dir,
                        prefix="nothing",
                        created=created,
                    )
                    _play(nothing)
                    continue

                accepted = _render(
                    (
                        "Verstanden. Ich starte ausschließlich die betroffenen "
                        "freigegebenen HomePi Dienste neu und prüfe anschließend den Status."
                    ),
                    audio_dir=audio_dir,
                    prefix="accepted",
                    created=created,
                )
                _play(accepted)
                _, result_path = _request_recovery(request_dir, result_dir)
                result_audio = _render(
                    _recovery_result_text(_wait_for_result(result_path)),
                    audio_dir=audio_dir,
                    prefix="recovery-result",
                    created=created,
                )
                _play(result_audio)
                continue

            if digit == "3":
                _play(alert_audio)
                continue

            if digit == "9" or digit == "":
                closing = _render(
                    "Bestätigt. Ich überwache das System weiter.",
                    audio_dir=audio_dir,
                    prefix="closing",
                    created=created,
                )
                _play(closing)
                break

            _play(invalid)

        _command("HANGUP")
        return 0
    except AgiError:
        return 1
    finally:
        for path in created:
            try:
                path.unlink()
            except OSError:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
