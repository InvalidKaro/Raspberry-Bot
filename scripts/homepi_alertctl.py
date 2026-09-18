from __future__ import annotations

import argparse
import asyncio
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.phone_alerts import (
    AlertSignal,
    AsteriskCallFileDialer,
    LocalSpeechRenderer,
    PhoneAlertConfig,
)
from services.phone_incidents import (
    IncidentStore,
    InteractiveAlertConfig,
    InteractiveAsteriskDialer,
)


def _load() -> tuple[PhoneAlertConfig, InteractiveAlertConfig]:
    load_dotenv(ROOT / ".env.alerts")
    return PhoneAlertConfig.from_env(), InteractiveAlertConfig.from_env()


def _registration_summary() -> str:
    binary = shutil.which("asterisk")
    if binary is None:
        return "Asterisk: not installed"
    try:
        result = subprocess.run(
            [binary, "-rx", "pjsip show registrations"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"Asterisk: unavailable ({type(exc).__name__})"
    text = (result.stdout or result.stderr or "").strip()
    if result.returncode != 0:
        return f"Asterisk: command failed\n{text[-1000:]}"
    lines = [
        line
        for line in text.splitlines()
        if line.strip()
        and not line.strip().startswith("<Registration/ServerURI")
    ]
    return "Asterisk registration:\n" + "\n".join(lines[-12:])


def doctor() -> int:
    phone, interactive = _load()
    print("HomePi Phone Assistant Doctor")
    print("=" * 32)
    print(f"alerts enabled:      {phone.enabled}")
    print(f"interactive mode:    {interactive.enabled}")
    print(f"target configured:   {bool(phone.target)}")
    print(f"PJSIP trunk:         {phone.pjsip_trunk}")
    print(f"AGI script:          {interactive.agi_script}")
    print(f"shared directory:    {interactive.root_dir}")
    print(f"outgoing spool:      {phone.outgoing_dir}")
    print(f"espeak-ng:           {shutil.which('espeak-ng') or 'missing'}")
    print(f"sox:                 {shutil.which('sox') or 'missing'}")
    print(f"asterisk:            {shutil.which('asterisk') or 'missing'}")

    problems: list[str] = []
    try:
        phone.validate_for_calling()
    except ValueError as exc:
        problems.append(str(exc))

    required_dirs = [phone.outgoing_dir]
    if interactive.enabled:
        required_dirs.extend(
            [
                interactive.root_dir,
                interactive.incidents_dir,
                interactive.actions_dir,
                interactive.acknowledgements_dir,
                interactive.audio_dir,
                interactive.call_staging_dir,
            ]
        )
        if not interactive.agi_script.is_file():
            problems.append(f"AGI script missing: {interactive.agi_script}")

    for path in required_dirs:
        if not path.is_dir():
            problems.append(f"Directory missing: {path}")

    for binary in ("asterisk", "espeak-ng", "sox"):
        if shutil.which(binary) is None:
            problems.append(f"Binary missing: {binary}")

    print()
    print(_registration_summary())
    print()
    if problems:
        print("Problems:")
        for problem in problems:
            print(f"- {problem}")
        return 1

    print("Doctor result: ready")
    return 0


def test_call() -> int:
    phone, interactive = _load()
    phone.validate_for_calling()
    message = (
        "Guten Tag. Dies ist ein Test des HomePi Assistenten. "
        "Es liegt kein realer Systemfehler vor."
    )

    if interactive.enabled:
        store = IncidentStore(interactive)
        store.ensure_directories()
        configured_phone = replace(phone, audio_dir=interactive.audio_dir)
        renderer = LocalSpeechRenderer(configured_phone)
        audio = renderer.render(message)
        incident = store.create(
            [
                AlertSignal(
                    key="test-call",
                    summary="Manual phone assistant test",
                    spoken=message,
                )
            ],
            audio_file=audio,
            restart_unit=None,
            test=True,
        )
        dialer = InteractiveAsteriskDialer(
            configured_phone,
            interactive,
            store,
        )
        queued = dialer.queue(incident)
        print(f"Interactive test call queued: {queued.name}")
        print(f"Incident: {incident.id}")
        return 0

    renderer = LocalSpeechRenderer(phone)
    audio = renderer.render(message)
    queued = AsteriskCallFileDialer(phone).queue(audio)
    print(f"Test call queued: {queued.name}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="HomePi automatic phone assistant control utility"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="Check local phone-assistant installation")
    sub.add_parser("test-call", help="Queue an explicit test call")
    args = parser.parse_args()

    if args.command == "doctor":
        return doctor()
    if args.command == "test-call":
        return test_call()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
