from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import re
import time
from collections import defaultdict, deque
from typing import Any

import psutil
from aiohttp import web

from homepi_intelligence import voice_blackbox, voice_score, voice_warnings
from .services.commands import run_command

VOICE_PATH = "/api/voice-command"
HELPER = "/usr/local/sbin/homepi-systemctl"
MAX_TEXT = 500
RATE_LIMIT = 30
RATE_WINDOW = 60.0
CONFIRM_TTL = 60.0

_SERVICE_ALIASES: list[tuple[tuple[str, ...], str]] = [
    (("display 2", "display zwei", "zweites display", "oled 2", "oled zwei"), "raspberry-display2"),
    (("meshtastic", "mesh service", "lora service"), "raspberry-meshtastic"),
    (("discord bot", "raspberry bot", "bot service", "der bot", "bot"), "raspberry-bot"),
    (("dashboard", "web dashboard"), "raspberry-dashboard"),
    (("display 1", "display eins", "erstes display", "oled 1", "oled eins", "display"), "raspberry-display"),
    (("pi hole", "pihole", "dns service"), "pihole-FTL"),
    (("tailscale", "tailscaled"), "tailscaled"),
]

_UNIT_NAMES = {
    "raspberry-bot": "Discord-Bot",
    "raspberry-bot.service": "Discord-Bot",
    "raspberry-dashboard": "Dashboard",
    "raspberry-dashboard.service": "Dashboard",
    "raspberry-display": "Display eins",
    "raspberry-display.service": "Display eins",
    "raspberry-display2": "Display zwei",
    "raspberry-display2.service": "Display zwei",
    "raspberry-meshtastic": "Meshtastic",
    "raspberry-meshtastic.service": "Meshtastic",
    "pihole-FTL": "Pi-hole",
    "pihole-FTL.service": "Pi-hole",
    "tailscaled": "Tailscale",
    "tailscaled.service": "Tailscale",
    "ssh": "SSH",
    "ssh.service": "SSH",
}

_UNIT_RE = re.compile(r"^[A-Za-z0-9_.@:-]{1,128}$")
_DIRECT_SYSTEMCTL_RE = re.compile(
    r"\bsystemctl\s+(start|stop|restart|reload|try-restart|status|enable|disable|mask|unmask|is-active|is-enabled)\s+([A-Za-z0-9_.@:-]+)\b",
    re.IGNORECASE,
)
_GENERIC_UNIT_RE = re.compile(r"\b(?:dienst|service|unit)\s+([A-Za-z0-9_.@:-]+)\b", re.IGNORECASE)
_UNIT_ACTION_WORDS: list[tuple[str, tuple[str, ...]]] = [
    ("try-restart", ("try restart", "try-restart")),
    ("restart", ("neu starten", "neustarten", "restart", "re starten")),
    ("is-active", ("ist aktiv", "is active", "is-active")),
    ("is-enabled", ("ist aktiviert", "is enabled", "is-enabled")),
    ("disable", ("deaktivieren", "deaktiviere", "disable")),
    ("enable", ("aktivieren", "aktiviere", "enable")),
    ("unmask", ("entmaskieren", "unmask")),
    ("mask", ("maskieren", "mask")),
    ("reload", ("neu laden", "reload")),
    ("stop", ("stoppen", "stoppe", "anhalten", "halte an", "stop")),
    ("start", ("starten", "starte", "start")),
    ("status", ("status", "zustand")),
]

_DANGEROUS_UNIT_ACTIONS = {"stop", "disable", "mask"}
_CONFIRM_ONLY = {
    "bestaetigen",
    "bestaetige",
    "ja bestaetigen",
    "ja bitte bestaetigen",
    "befehl bestaetigen",
    "ausfuehren",
    "jetzt ausfuehren",
    "ja wirklich",
}
_CANCEL_ONLY = {"abbrechen", "abbruch", "nicht ausfuehren", "nein abbrechen", "stornieren"}
_rate_buckets: dict[str, deque[float]] = defaultdict(deque)
_pending_confirmations: dict[str, dict[str, Any]] = {}


def _normalise(text: str) -> str:
    value = text.lower().strip()
    value = value.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    value = re.sub(r"[!?;,.:]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return re.sub(
        r"\b(?:home\s*(?:pi|pie|pai|pei|p)|homepi|homepie|homepai|homepei|hompi)\b",
        "homepi",
        value,
    )


def _token() -> str:
    return os.getenv("VOICE_API_TOKEN", "").strip()


def _supplied_token(request: web.Request) -> str:
    supplied = request.headers.get("Authorization", "").strip()
    if supplied.lower().startswith("bearer "):
        return supplied[7:].strip()
    return request.headers.get("X-HomePi-Token", "").strip()


def _authorised(request: web.Request) -> bool:
    expected = _token()
    supplied = _supplied_token(request)
    return len(expected) >= 24 and bool(supplied) and hmac.compare_digest(supplied, expected)


def _client_key(request: web.Request) -> str:
    digest = hashlib.sha256(_supplied_token(request).encode("utf-8")).hexdigest()
    return f"{request.remote or 'unknown'}:{digest}"


def _rate_allowed(request: web.Request) -> bool:
    key = request.remote or "unknown"
    now = time.monotonic()
    bucket = _rate_buckets[key]
    while bucket and now - bucket[0] > RATE_WINDOW:
        bucket.popleft()
    if len(bucket) >= RATE_LIMIT:
        return False
    bucket.append(now)
    return True


def _explicit_confirmation(data: dict[str, Any], normalised: str) -> bool:
    if data.get("confirm") is True:
        return True
    return any(
        phrase in normalised
        for phrase in ("bestaetigen", "ich bestaetige", "wirklich ausfuehren", "wirklich machen", "ja wirklich")
    )


def _pending_for(request: web.Request) -> dict[str, Any] | None:
    key = _client_key(request)
    pending = _pending_confirmations.get(key)
    if not pending:
        return None
    if time.monotonic() - float(pending.get("created_at", 0.0)) > CONFIRM_TTL:
        _pending_confirmations.pop(key, None)
        return None
    return pending


def _store_pending(request: web.Request, action: str, unit: str | None) -> None:
    _pending_confirmations[_client_key(request)] = {"action": action, "unit": unit, "created_at": time.monotonic()}


def _clear_pending(request: web.Request) -> None:
    _pending_confirmations.pop(_client_key(request), None)


def _service_from_text(text: str, normalised: str) -> tuple[str | None, bool]:
    direct = _DIRECT_SYSTEMCTL_RE.search(text)
    if direct:
        unit = direct.group(2)
        return (unit if _UNIT_RE.fullmatch(unit) else None), False
    generic = _GENERIC_UNIT_RE.search(text)
    if generic:
        unit = generic.group(1)
        return (unit if _UNIT_RE.fullmatch(unit) else None), False
    for aliases, unit in _SERVICE_ALIASES:
        if any(alias in normalised for alias in aliases):
            return unit, True
    return None, False


def _unit_action(text: str, normalised: str) -> str | None:
    direct = _DIRECT_SYSTEMCTL_RE.search(text)
    if direct:
        return direct.group(1).lower()
    if re.search(r"\b(?:starte|start|starten)\b.*\bneu\b", normalised):
        return "restart"
    if re.search(r"\bneu\b.*\b(?:starte|start|starten)\b", normalised):
        return "restart"
    for action, phrases in _UNIT_ACTION_WORDS:
        if any(phrase in normalised for phrase in phrases):
            return action
    return None


def _system_power_action(normalised: str) -> str | None:
    if any(
        value in normalised
        for value in (
            "homepi herunterfahren",
            "pi herunterfahren",
            "server herunterfahren",
            "system herunterfahren",
            "raspberry herunterfahren",
            "shutdown",
            "poweroff",
            "ausschalten",
        )
    ):
        return "poweroff"
    if any(
        value in normalised
        for value in (
            "homepi neu starten",
            "pi neu starten",
            "server neu starten",
            "system neu starten",
            "raspberry pi neu starten",
            "raspberry neu starten",
            "reboot homepi",
            "reboot system",
            "reboot server",
        )
    ) or normalised in {"reboot", "neustart", "neu starten", "starte neu"}:
        return "reboot"
    return None


def _needs_confirmation(action: str, unit: str | None, known_alias: bool) -> bool:
    if action in {"reboot", "poweroff"} or action in _DANGEROUS_UNIT_ACTIONS:
        return True
    return bool(action in {"restart", "reload", "enable", "unmask"} and unit and not known_alias)


def _friendly_unit(unit: str | None) -> str:
    if not unit:
        return "System"
    if unit in _UNIT_NAMES:
        return _UNIT_NAMES[unit]
    clean = unit[:-8] if unit.endswith(".service") else unit
    return clean.replace("_", " ")


def _action_label(action: str) -> str:
    return {
        "start": "starten",
        "stop": "stoppen",
        "restart": "neu starten",
        "try-restart": "bei Bedarf neu starten",
        "reload": "neu laden",
        "enable": "für den Autostart aktivieren",
        "disable": "aus dem Autostart entfernen",
        "mask": "maskieren",
        "unmask": "entmaskieren",
        "status": "prüfen",
        "is-active": "auf Aktivität prüfen",
        "is-enabled": "auf Autostart prüfen",
    }.get(action, action)


def _success_action_speech(action: str, unit: str) -> str:
    name = _friendly_unit(unit)
    return {
        "start": f"Erledigt. {name} ist gestartet.",
        "stop": f"Erledigt. {name} wurde gestoppt.",
        "restart": f"Erledigt. {name} wurde sauber neu gestartet.",
        "try-restart": f"Erledigt. {name} wurde geprüft und bei Bedarf neu gestartet.",
        "reload": f"Erledigt. {name} hat seine Konfiguration neu eingelesen.",
        "enable": f"Erledigt. {name} startet künftig automatisch mit dem System.",
        "disable": f"Erledigt. Der Autostart für {name} ist deaktiviert.",
        "mask": f"Erledigt. {name} ist jetzt maskiert.",
        "unmask": f"Erledigt. {name} ist wieder freigegeben.",
    }.get(action, f"Erledigt. Die Aktion für {name} wurde ausgeführt.")


async def _helper(action: str, unit: str | None = None, *, timeout: float = 30.0) -> dict[str, Any]:
    command = ["sudo", "-n", HELPER, action]
    if unit:
        command.append(unit)
    result = await run_command(command, timeout=timeout)
    output = (result.stdout or result.stderr or "").strip()
    return {"ok": result.ok, "output": output[:8000]}


async def _delayed_helper(action: str, unit: str | None = None) -> None:
    await asyncio.sleep(1.0)
    await _helper(action, unit, timeout=20.0)


def _duration_speech(hours: int, minutes: int) -> str:
    parts: list[str] = []
    if hours:
        parts.append(f"{hours} {'Stunde' if hours == 1 else 'Stunden'}")
    if minutes or not parts:
        parts.append(f"{minutes} {'Minute' if minutes == 1 else 'Minuten'}")
    return " und ".join(parts)


def _system_summary() -> str:
    vm = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    cpu = psutil.cpu_percent(interval=None)
    uptime = max(0, int(time.time() - psutil.boot_time()))
    hours, remainder = divmod(uptime, 3600)
    minutes = remainder // 60
    temp = None
    for path in ("/sys/class/thermal/thermal_zone0/temp", "/sys/class/hwmon/hwmon0/temp1_input"):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                temp = float(handle.read().strip())
            if temp > 1000:
                temp /= 1000.0
            break
        except (OSError, ValueError):
            continue

    warnings: list[str] = []
    if cpu >= 85:
        warnings.append("die CPU ist deutlich ausgelastet")
    if vm.percent >= 85:
        warnings.append("der Arbeitsspeicher wird knapp")
    if disk.percent >= 90:
        warnings.append("der Speicherplatz wird knapp")
    if temp is not None and temp >= 70:
        warnings.append("die Temperatur ist erhöht")

    temp_text = ""
    if temp is not None:
        temp_text = f" und die Kerntemperatur bei {temp:.1f} Grad".replace(".", ",")
    metrics = (
        f"CPU bei {cpu:.0f} Prozent, Arbeitsspeicher bei {vm.percent:.0f} Prozent, "
        f"Speicher bei {disk.percent:.0f} Prozent{temp_text}."
    )
    health = " Alles im grünen Bereich." if not warnings else " Auffällig ist: " + "; ".join(warnings) + "."
    return f"Systemcheck abgeschlossen. {metrics} Laufzeit: {_duration_speech(hours, minutes)}.{health}"


def _command_catalog() -> dict[str, list[str]]:
    return {
        "intelligence": [
            "Blackbox",
            "Was ist heute passiert?",
            "Was ist letzte Nacht passiert?",
            "Server Score",
            "Wie geht es dem Server?",
            "Warnzentrale",
            "Gibt es Warnungen?",
            "Gibt es eine Unwetterwarnung?",
        ],
        "status": ["Status", "Wie warm ist der Pi?", "CPU Auslastung", "RAM Auslastung", "Speicher Auslastung", "Uptime"],
        "homepi_services": [
            "Starte den Bot neu",
            "Starte das Dashboard neu",
            "Starte Display eins neu",
            "Starte Display zwei neu",
            "Starte Meshtastic neu",
            "Starte Pi-hole neu",
            "Starte Tailscale neu",
        ],
        "systemd": [
            "Liste Dienste",
            "Systemd neu laden",
            "Status Dienst ssh",
            "Dienst nginx starten",
            "Dienst nginx neu starten",
            "Dienst nginx stoppen",
            "Dienst nginx aktivieren",
            "Dienst nginx deaktivieren",
            "Dienst nginx maskieren",
            "Dienst nginx entmaskieren",
            "systemctl restart cron",
        ],
        "power": ["Pi neu starten", "Pi herunterfahren"],
        "confirmation": ["Bestätigen", "Abbrechen"],
    }


def _confirmation_response(request: web.Request, action: str, unit: str | None = None) -> web.Response:
    _store_pending(request, action, unit)
    if action == "reboot":
        target = "den kompletten HomePi neu starten"
    elif action == "poweroff":
        target = "den kompletten HomePi herunterfahren"
    else:
        target = f"{_friendly_unit(unit)} {_action_label(action)}"
    speech = (
        f"Verstanden. Das würde {target}. Aus Sicherheitsgründen brauche ich dafür deine Bestätigung. "
        f"Ich halte den Befehl {int(CONFIRM_TTL)} Sekunden bereit. Sage einfach: Bestätigen. Oder: Abbrechen."
    )
    return web.json_response(
        {
            "ok": False,
            "confirmation_required": True,
            "action": action,
            "unit": unit,
            "confirmation_ttl_seconds": int(CONFIRM_TTL),
            "speech": speech,
        },
        status=200,
    )


async def _execute_action(action: str, unit: str | None = None) -> web.Response:
    if action in {"reboot", "poweroff"}:
        asyncio.create_task(_delayed_helper(action), name=f"voice-{action}")
        speech = (
            "Bestätigt. Ich starte HomePi jetzt neu. Die Verbindung wird für einen Moment unterbrochen."
            if action == "reboot"
            else "Bestätigt. Ich fahre HomePi jetzt kontrolliert herunter."
        )
        return web.json_response({"ok": True, "speech": speech, "action": action, "scheduled": True})

    if not unit:
        return web.json_response({"ok": False, "speech": "Mir fehlt noch der Name des Dienstes."}, status=400)

    if unit in {"raspberry-dashboard", "raspberry-dashboard.service"} and action in {"restart", "stop"}:
        asyncio.create_task(_delayed_helper(action, unit), name=f"voice-{action}-dashboard")
        speech = (
            "Erledigt. Ich starte das Dashboard jetzt neu. Die Verbindung kann kurz unterbrochen sein."
            if action == "restart"
            else "Erledigt. Das Dashboard wird jetzt gestoppt."
        )
        return web.json_response({"ok": True, "speech": speech, "action": action, "unit": unit, "scheduled": True})

    result = await _helper(action, unit)
    name = _friendly_unit(unit)
    output = str(result["output"] or "").strip().lower()
    if action == "status":
        speech = f"{name} ist aktiv und läuft ordnungsgemäß." if result["ok"] else f"{name} ist derzeit nicht aktiv."
    elif action == "is-active":
        speech = f"{name} ist aktiv." if output == "active" or result["ok"] else f"{name} ist aktuell nicht aktiv."
    elif action == "is-enabled":
        speech = (
            f"{name} ist für den automatischen Start eingerichtet."
            if output == "enabled" or result["ok"]
            else f"{name} ist nicht für den automatischen Start eingerichtet."
        )
    elif result["ok"]:
        speech = _success_action_speech(action, unit)
    else:
        speech = f"Das hat nicht geklappt. Ich konnte {name} nicht {_action_label(action)}."
    return web.json_response(
        {"ok": result["ok"], "speech": speech, "action": action, "unit": unit, "detail": result["output"]},
        status=200 if result["ok"] else 500,
    )


def _intelligence_command(normalised: str) -> web.Response | None:
    blackbox_phrases = (
        "blackbox",
        "black box",
        "was ist heute passiert",
        "was war heute los",
        "letzte ereignisse",
        "was ist letzte nacht passiert",
        "was war letzte nacht los",
    )
    if any(phrase in normalised for phrase in blackbox_phrases):
        hours = 12.0 if "nacht" in normalised else 24.0
        speech, data = voice_blackbox(hours)
        return web.json_response({"ok": True, "speech": speech, "command": "blackbox", "blackbox": data})

    score_phrases = (
        "server score",
        "server-score",
        "health score",
        "wie geht es dem server",
        "wie gehts dem server",
        "server gesundheit",
        "system score",
    )
    if any(phrase in normalised for phrase in score_phrases):
        speech, data = voice_score()
        return web.json_response({"ok": True, "speech": speech, "command": "server-score", "score": data})

    warning_phrases = (
        "warnzentrale",
        "gibt es warnungen",
        "welche warnungen",
        "aktive warnungen",
        "unwetterwarnung",
        "amtliche warnung",
        "nina warnung",
        "bevoelkerungswarnung",
    )
    if any(phrase in normalised for phrase in warning_phrases):
        speech, data = voice_warnings()
        return web.json_response({"ok": True, "speech": speech, "command": "warnings", "warnings": data})
    return None


async def api_voice_command(request: web.Request) -> web.Response:
    if not _token():
        return web.json_response(
            {"ok": False, "speech": "Die Sprachsteuerung ist noch nicht vollständig eingerichtet. Mir fehlt der API-Token."},
            status=503,
        )
    if not _authorised(request):
        return web.json_response(
            {"ok": False, "speech": "Die Authentifizierung ist fehlgeschlagen. Bitte prüfe den Voice-API-Token."},
            status=401,
        )
    if not _rate_allowed(request):
        return web.json_response({"ok": False, "speech": "Einen Moment bitte. Es kamen zu viele Befehle in kurzer Zeit."}, status=429)

    try:
        data = await request.json()
    except Exception:
        return web.json_response({"ok": False, "speech": "Die Anfrage kam unvollständig an. Versuch es bitte noch einmal."}, status=400)
    if not isinstance(data, dict):
        return web.json_response({"ok": False, "speech": "Mit dieser Anfrage kann ich nichts anfangen."}, status=400)

    text = str(data.get("text") or "").strip()
    if not text or len(text) > MAX_TEXT:
        return web.json_response({"ok": False, "speech": "Ich habe keinen eindeutigen Sprachbefehl erhalten."}, status=400)

    normalised = _normalise(text)
    pending = _pending_for(request)
    if pending and normalised in _CONFIRM_ONLY:
        _clear_pending(request)
        return await _execute_action(str(pending["action"]), pending.get("unit"))
    if pending and normalised in _CANCEL_ONLY:
        _clear_pending(request)
        return web.json_response({"ok": True, "cancelled": True, "speech": "Verstanden. Der kritische Befehl wurde verworfen."})
    if pending:
        _clear_pending(request)

    confirmed = _explicit_confirmation(data, normalised)

    if any(phrase in normalised for phrase in ("hilfe", "befehle", "commands", "was kannst du", "welche befehle", "befehlsliste")):
        return web.json_response(
            {
                "ok": True,
                "speech": (
                    "Natürlich. Neben Systemstatus und Dienststeuerung kann ich jetzt auch die Blackbox auswerten, "
                    "den Server Score nennen und die regionale Warnzentrale prüfen. Kritische Aktionen bestätigst du weiterhin separat."
                ),
                "command": "help",
                "commands": _command_catalog(),
            }
        )

    intelligence = _intelligence_command(normalised)
    if intelligence is not None:
        return intelligence

    if normalised in {"status", "homepi status", "server status", "system status", "pi status"} or any(
        word in normalised for word in ("temperatur", "wie warm", "cpu", "ram auslastung", "speicher auslastung", "uptime")
    ):
        return web.json_response({"ok": True, "speech": _system_summary(), "command": "system-summary"})

    if any(phrase in normalised for phrase in ("liste dienste", "dienste auflisten", "services auflisten", "service liste")):
        result = await _helper("list", timeout=20.0)
        if not result["ok"]:
            return web.json_response({"ok": False, "speech": "Ich konnte die Diensteliste gerade nicht zuverlässig abrufen."}, status=500)
        lines = [line for line in result["output"].splitlines() if line.strip()]
        active = sum(" active " in f" {line} " for line in lines)
        return web.json_response(
            {
                "ok": True,
                "speech": f"Bestandsaufnahme abgeschlossen. Ich sehe {len(lines)} Dienste, davon ungefähr {active} aktiv.",
                "services": lines[:250],
            }
        )

    if any(phrase in normalised for phrase in ("systemd neu laden", "daemon reload", "daemon-reload")):
        result = await _helper("daemon-reload")
        speech = "Erledigt. Systemd hat seine Konfiguration neu eingelesen." if result["ok"] else "Die Systemd-Konfiguration konnte nicht neu eingelesen werden."
        return web.json_response({"ok": result["ok"], "speech": speech, "detail": result["output"]}, status=200 if result["ok"] else 500)

    power_action = _system_power_action(normalised)
    if power_action:
        if not confirmed:
            return _confirmation_response(request, power_action)
        _clear_pending(request)
        return await _execute_action(power_action)

    unit, known_alias = _service_from_text(text, normalised)
    action = _unit_action(text, normalised)
    if unit and action:
        if _needs_confirmation(action, unit, known_alias) and not confirmed:
            return _confirmation_response(request, action, unit)
        _clear_pending(request)
        return await _execute_action(action, unit)

    return web.json_response(
        {
            "ok": False,
            "speech": "Das konnte ich nicht eindeutig zuordnen. Sag Befehle für eine Übersicht. Zum Beispiel: Server Score oder Meshtastic neu starten.",
            "text": text,
        },
        status=400,
    )


def register_voice_routes(app: web.Application) -> None:
    app.router.add_post(VOICE_PATH, api_voice_command)
