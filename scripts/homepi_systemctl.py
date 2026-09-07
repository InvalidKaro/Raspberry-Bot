#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

SYSTEMCTL = "/usr/bin/systemctl"
EVENT_PATH = Path("/home/stefano/services/Raspberry-Bot/data/voice_display_event.json")
UNIT_RE = re.compile(r"^[A-Za-z0-9_.@:-]{1,128}$")
UNIT_ACTIONS = {
    "start",
    "stop",
    "restart",
    "reload",
    "try-restart",
    "status",
    "enable",
    "disable",
    "mask",
    "unmask",
    "is-active",
    "is-enabled",
}
SYSTEM_ACTIONS = {"reboot", "poweroff", "daemon-reload", "list"}


def _write_event(action: str, unit: str | None, status: str, detail: str = "") -> None:
    payload = {
        "text": "",
        "action": action,
        "unit": unit or "",
        "status": status,
        "speech": detail[:500],
        "created_at": time.time(),
    }
    try:
        EVENT_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = EVENT_PATH.with_suffix(EVENT_PATH.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(EVENT_PATH)
        # Keep the event writable for the normal HomePi user even though this
        # helper itself runs through sudo.
        parent = EVENT_PATH.parent.stat()
        os.chown(EVENT_PATH, parent.st_uid, parent.st_gid)
        os.chmod(EVENT_PATH, 0o664)
    except OSError:
        pass


def fail(message: str, code: int = 2) -> int:
    print(message, file=sys.stderr)
    return code


def clean_unit(raw: str) -> str:
    unit = raw.strip()
    if not UNIT_RE.fullmatch(unit):
        raise ValueError("Ungültiger systemd-Unit-Name.")
    if "." not in unit:
        unit += ".service"
    return unit


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        return fail("Usage: homepi-systemctl <action> [unit]")

    action = argv[1].strip().lower()
    unit: str | None = None
    if action in UNIT_ACTIONS:
        if len(argv) != 3:
            return fail(f"{action} benötigt genau eine Unit.")
        try:
            unit = clean_unit(argv[2])
        except ValueError as exc:
            return fail(str(exc))
        command = [SYSTEMCTL, action, unit, "--no-pager"]
    elif action == "daemon-reload":
        if len(argv) != 2:
            return fail("daemon-reload erwartet keine weiteren Argumente.")
        command = [SYSTEMCTL, "daemon-reload"]
    elif action in {"reboot", "poweroff"}:
        if len(argv) != 2:
            return fail(f"{action} erwartet keine weiteren Argumente.")
        command = [SYSTEMCTL, action]
    elif action == "list":
        if len(argv) != 2:
            return fail("list erwartet keine weiteren Argumente.")
        command = [SYSTEMCTL, "list-units", "--type=service", "--all", "--no-legend", "--no-pager", "--plain"]
    else:
        return fail("Nicht erlaubte systemctl-Aktion.")

    _write_event(action, unit, "RUNNING")
    try:
        proc = subprocess.run(command, text=True, capture_output=True, timeout=45, check=False)
    except subprocess.TimeoutExpired:
        _write_event(action, unit, "TIMEOUT", "systemctl Zeitüberschreitung")
        return fail("systemctl Zeitüberschreitung.", 124)

    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    if stdout:
        print(stdout[:12000])
    if stderr:
        print(stderr[:12000], file=sys.stderr)
    _write_event(action, unit, "OK" if proc.returncode == 0 else "ERROR", stderr or stdout)
    return int(proc.returncode)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
