from __future__ import annotations

import asyncio
import hmac
import os
import re
import time
from collections import defaultdict, deque
from typing import Any

import psutil
from aiohttp import web

from .services.commands import run_command

VOICE_PATH = "/api/voice-command"
HELPER = "/usr/local/sbin/homepi-systemctl"
MAX_TEXT = 500
RATE_LIMIT = 30
RATE_WINDOW = 60.0

_SERVICE_ALIASES: list[tuple[tuple[str, ...], str]] = [
    (("display 2", "display zwei", "zweites display", "oled 2", "oled zwei"), "raspberry-display2"),
    (("meshtastic", "mesh service", "lora service"), "raspberry-meshtastic"),
    (("discord bot", "raspberry bot", "bot service", "der bot", "bot"), "raspberry-bot"),
    (("dashboard", "web dashboard"), "raspberry-dashboard"),
    (("display 1", "display eins", "erstes display", "oled 1", "oled eins", "display"), "raspberry-display"),
    (("pi hole", "pihole", "dns service"), "pihole-FTL"),
    (("tailscale", "tailscaled"), "tailscaled"),
]

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
_rate_buckets: dict[str, deque[float]] = defaultdict(deque)


def _normalise(text: str) -> str:
    value = text.lower().strip()
    value = value.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    value = re.sub(r"[!?;,]+", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value


def _token() -> str:
    return os.getenv("VOICE_API_TOKEN", "").strip()


def _authorised(request: web.Request) -> bool:
    expected = _token()
    if len(expected) < 24:
        return False
    supplied = request.headers.get("Authorization", "").strip()
    if supplied.lower().startswith("bearer "):
        supplied = supplied[7:].strip()
    else:
        supplied = request.headers.get("X-HomePi-Token", "").strip()
    return bool(supplied) and hmac.compare_digest(supplied, expected)


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
        for phrase in (
            "bestaetigen",
            "ich bestaetige",
            "wirklich ausfuehren",
            "wirklich machen",
            "ja wirklich",
        )
    )


def _service_from_text(text: str, normalised: str) -> tuple[str | None, bool]:
    direct = _DIRECT_SYSTEMCTL_RE.search(text)
    if direct:
        unit = direct.group(2)
        return unit if _UNIT_RE.fullmatch(unit) else None, False

    generic = _GENERIC_UNIT_RE.search(text)
    if generic:
        unit = generic.group(1)
        return unit if _UNIT_RE.fullmatch(unit) else None, False

    for aliases, unit in _SERVICE_ALIASES:
        if any(alias in normalised for alias in aliases):
            return unit, True
    return None, False


def _unit_action(text: str, normalised: str) -> str | None:
    direct = _DIRECT_SYSTEMCTL_RE.search(text)
    if direct:
        return direct.group(1).lower()
    for action, phrases in _UNIT_ACTION_WORDS:
        if any(phrase in normalised for phrase in phrases):
            return action
    return None


def _system_power_action(normalised: str) -> str | None:
    poweroff_phrases = (
        "homepi herunterfahren",
        "home pi herunterfahren",
        "pi herunterfahren",
        "system herunterfahren",
        "shutdown",
        "poweroff",
        "ausschalten",
    )
    reboot_phrases = (
        "homepi neu starten",
        "home pi neu starten",
        "pi neu starten",
        "system neu starten",
        "raspberry pi neu starten",
        "reboot homepi",
        "reboot system",
    )
    if any(value in normalised for value in poweroff_phrases):
        return "poweroff"
    if any(value in normalised for value in reboot_phrases):
        return "reboot"
    if normalised in {"reboot", "neustart", "neu starten"}:
        return "reboot"
    return None


def _needs_confirmation(action: str, unit: str | None, known_alias: bool) -> bool:
    if action in {"reboot", "poweroff"}:
        return True
    if action in _DANGEROUS_UNIT_ACTIONS:
        return True
    if action in {"restart", "reload", "enable", "unmask"} and unit and not known_alias:
        return True
    return False


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
    temp_text = f", Temperatur {temp:.1f} Grad" if temp is not None else ""
    return (
        f"CPU {cpu:.0f} Prozent, RAM {vm.percent:.0f} Prozent, "
        f"Speicher {disk.percent:.0f} Prozent{temp_text}, Uptime {hours} Stunden {minutes} Minuten."
    )


def _confirmation_response(action: str, unit: str | None = None) -> web.Response:
    target = f" für {unit}" if unit else ""
    speech = f"Das ist ein kritischer Befehl: {action}{target}. Sage den Befehl erneut mit dem Wort bestätigen."
    return web.json_response(
        {
            "ok": False,
            "confirmation_required": True,
            "action": action,
            "unit": unit,
            "speech": speech,
        },
        status=409,
    )


async def api_voice_command(request: web.Request) -> web.Response:
    if not _token():
        return web.json_response({"ok": False, "speech": "Voice API ist noch nicht eingerichtet."}, status=503)
    if not _authorised(request):
        return web.json_response({"ok": False, "speech": "Voice API Token ist ungültig."}, status=401)
    if not _rate_allowed(request):
        return web.json_response({"ok": False, "speech": "Zu viele Befehle. Kurz warten."}, status=429)

    try:
        data = await request.json()
    except Exception:
        return web.json_response({"ok": False, "speech": "Ich konnte den Sprachbefehl nicht lesen."}, status=400)
    if not isinstance(data, dict):
        return web.json_response({"ok": False, "speech": "Ungültiger Request."}, status=400)

    text = str(data.get("text") or "").strip()
    if not text or len(text) > MAX_TEXT:
        return web.json_response({"ok": False, "speech": "Der Sprachbefehl ist leer oder zu lang."}, status=400)

    normalised = _normalise(text)
    confirmed = _explicit_confirmation(data, normalised)

    # Fast read-only commands first.
    if normalised in {"status", "homepi status", "home pi status", "system status"} or any(
        word in normalised for word in ("temperatur", "wie warm", "cpu", "ram auslastung", "speicher auslastung", "uptime")
    ):
        speech = _system_summary()
        return web.json_response({"ok": True, "speech": speech, "command": "system-summary"})

    if any(phrase in normalised for phrase in ("liste dienste", "dienste auflisten", "services auflisten")):
        result = await _helper("list", timeout=20.0)
        if not result["ok"]:
            return web.json_response({"ok": False, "speech": "Ich konnte die Dienste nicht auflisten.", "detail": result["output"]}, status=500)
        lines = [line for line in result["output"].splitlines() if line.strip()]
        active = sum(" active " in f" {line} " for line in lines)
        return web.json_response(
            {"ok": True, "speech": f"Ich sehe {len(lines)} Services, davon ungefähr {active} aktiv.", "services": lines[:250]}
        )

    if any(phrase in normalised for phrase in ("systemd neu laden", "daemon reload", "daemon-reload")):
        result = await _helper("daemon-reload")
        speech = "Systemd wurde neu geladen." if result["ok"] else "Systemd konnte nicht neu geladen werden."
        return web.json_response({"ok": result["ok"], "speech": speech, "detail": result["output"]}, status=200 if result["ok"] else 500)

    power_action = _system_power_action(normalised)
    if power_action:
        if not confirmed:
            return _confirmation_response(power_action)
        asyncio.create_task(_delayed_helper(power_action), name=f"voice-{power_action}")
        speech = "HomePi wird neu gestartet." if power_action == "reboot" else "HomePi wird heruntergefahren."
        return web.json_response({"ok": True, "speech": speech, "action": power_action, "scheduled": True})

    unit, known_alias = _service_from_text(text, normalised)
    action = _unit_action(text, normalised)
    if unit and action:
        if _needs_confirmation(action, unit, known_alias) and not confirmed:
            return _confirmation_response(action, unit)

        # Restarting/stopping the dashboard itself would kill the HTTP response.
        if unit in {"raspberry-dashboard", "raspberry-dashboard.service"} and action in {"restart", "stop"}:
            asyncio.create_task(_delayed_helper(action, unit), name=f"voice-{action}-dashboard")
            return web.json_response(
                {
                    "ok": True,
                    "speech": f"Dashboard {action} ist eingeplant.",
                    "action": action,
                    "unit": unit,
                    "scheduled": True,
                }
            )

        result = await _helper(action, unit)
        if action == "status":
            speech = f"Status für {unit} wurde gelesen."
        elif action in {"is-active", "is-enabled"}:
            speech = result["output"] or f"Status für {unit} ist unbekannt."
        else:
            speech = f"{unit}: {action} erfolgreich." if result["ok"] else f"{unit}: {action} ist fehlgeschlagen."
        return web.json_response(
            {"ok": result["ok"], "speech": speech, "action": action, "unit": unit, "detail": result["output"]},
            status=200 if result["ok"] else 500,
        )

    return web.json_response(
        {
            "ok": False,
            "speech": "Diesen Befehl kenne ich noch nicht. Für beliebige Dienste sage zum Beispiel: Dienst nginx neu starten.",
            "text": text,
        },
        status=400,
    )


def register_voice_routes(app: web.Application) -> None:
    app.router.add_post(VOICE_PATH, api_voice_command)
