"""Parse compact multi-person BWG and induction entries."""
from __future__ import annotations

import re

METRIC = re.compile(
    r"(?i)(?<!\\w)(?:(bwg|ew|einweisung(?:en)?)\\s*[:=]?\\s*(\\d+)|(\\d+)\\s*(bwg|ew|einweisung(?:en)?))(?!\\w)"
)


def parse_multi_entries(text: str) -> list[tuple[str, int, int]]:
    """Return (employee, bwg, inductions); reject ambiguous or malformed input."""
    if len(text) > 3500:
        raise ValueError("Eingabe zu lang (max. 3500 Zeichen).")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines or len(lines) > 25:
        raise ValueError("Bitte 1 bis 25 Mitarbeiterzeilen eingeben.")
    entries = []
    seen = set()
    for number, line in enumerate(lines, 1):
        matches = list(METRIC.finditer(line))
        if not matches:
            raise ValueError(f"Zeile {number}: Keine BWG/EW-Angabe gefunden.")
        name = line[:matches[0].start()].strip(" :-,")
        if not name or len(name) > 100:
            raise ValueError(f"Zeile {number}: Mitarbeitername fehlt oder ist zu lang.")
        remainder = line[matches[0].start():]
        cursor = 0
        values = {}
        for match in matches:
            if line[matches[0].start() + cursor:match.start()].strip(" ,;/"):
                raise ValueError(f"Zeile {number}: Ungültiger Text zwischen den Werten.")
            key = (match.group(1) or match.group(4)).lower()
            key = "bwg" if key == "bwg" else "ew"
            if key in values:
                raise ValueError(f"Zeile {number}: {key.upper()} doppelt angegeben.")
            value = int(match.group(2) or match.group(3))
            if value > 999:
                raise ValueError(f"Zeile {number}: Maximal 999 pro Wert.")
            values[key] = value
            cursor = match.end() - matches[0].start()
        if remainder[cursor:].strip(" ,;/"):
            raise ValueError(f"Zeile {number}: Unbekannte Angabe am Zeilenende.")
        if not any(values.values()):
            raise ValueError(f"Zeile {number}: Mindestens ein Wert muss größer als 0 sein.")
        key = name.casefold()
        if key in seen:
            raise ValueError(f"Zeile {number}: {name} mehrfach angegeben.")
        seen.add(key)
        entries.append((name, values.get("bwg", 0), values.get("ew", 0)))
    return entries
