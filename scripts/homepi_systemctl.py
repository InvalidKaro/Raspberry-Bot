#!/usr/bin/env python3
from __future__ import annotations

import re
import subprocess
import sys

SYSTEMCTL = "/usr/bin/systemctl"
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

    try:
        proc = subprocess.run(command, text=True, capture_output=True, timeout=45, check=False)
    except subprocess.TimeoutExpired:
        return fail("systemctl Zeitüberschreitung.", 124)

    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    if stdout:
        print(stdout[:12000])
    if stderr:
        print(stderr[:12000], file=sys.stderr)
    return int(proc.returncode)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
