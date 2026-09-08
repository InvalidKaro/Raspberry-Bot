from __future__ import annotations

import os
import sqlite3
import time
from typing import Any

from PIL import Image, ImageDraw

from homepi_intelligence import read_state as read_intelligence_state
from . import secondary as core


INTELLIGENCE_PAGES = ("score", "blackbox", "warnings")
_state_cache: tuple[float, dict[str, Any]] = (0.0, {})


def _unexpected_errors_24h() -> int:
    """Count only unexpected bot exceptions, not normal command failures."""
    try:
        with core._connect() as con:
            if not core._table_exists(con, "dashboard_error_events"):
                return 0
            row = con.execute(
                "SELECT COUNT(*) count FROM dashboard_error_events "
                "WHERE created_at>=datetime('now','-24 hours')"
            ).fetchone()
            return core._as_int(row["count"] if row else 0)
    except (sqlite3.Error, OSError):
        return 0


_base_read_discord_stats = core._read_discord_stats
_base_alert = core.Display2Service._alert


def _read_discord_stats() -> dict[str, Any]:
    stats = _base_read_discord_stats()
    stats["errors_24h"] = _unexpected_errors_24h()
    return stats


def _intelligence_state() -> dict[str, Any]:
    global _state_cache
    now = time.monotonic()
    if now - _state_cache[0] < 2.0:
        return _state_cache[1]
    try:
        state = read_intelligence_state()
    except Exception:
        state = _state_cache[1]
    _state_cache = (now, state if isinstance(state, dict) else {})
    return _state_cache[1]


def _new_image() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("1", (128, 64), 0)
    return image, ImageDraw.Draw(image)


def _finish(image: Image.Image) -> Image.Image:
    return image.rotate(180) if core.ROTATION == 180 else image


def _render_score() -> Image.Image:
    image, draw = _new_image()
    state = _intelligence_state()
    score = state.get("score") if isinstance(state.get("score"), dict) else {}
    if not score:
        core._header(draw, "SERVER SCORE", "WAIT")
        draw.text((2, 21), "Noch keine Daten", font=core.FONT_MEDIUM, fill=255)
        draw.text((2, 43), "Blackbox startet...", font=core.FONT_TINY, fill=255)
        return _finish(image)

    overall = core._as_int(score.get("overall"))
    status = str(score.get("status") or "--")
    core._header(draw, "SERVER SCORE", status[:7])
    draw.text((2, 17), str(overall), font=core.FONT_LARGE, fill=255)
    draw.text((38, 25), "/100", font=core.FONT_SMALL, fill=255)
    draw.text((70, 17), f"SYS {core._as_int(score.get('system'))}", font=core.FONT_TINY, fill=255)
    draw.text((70, 28), f"NET {core._as_int(score.get('network'))}", font=core.FONT_TINY, fill=255)
    draw.text((2, 48), f"SVC {core._as_int(score.get('services'))}", font=core.FONT_TINY, fill=255)
    draw.text((70, 48), f"STAB {core._as_int(score.get('stability'))}", font=core.FONT_TINY, fill=255)
    return _finish(image)


def _render_blackbox() -> Image.Image:
    image, draw = _new_image()
    state = _intelligence_state()
    box = state.get("blackbox") if isinstance(state.get("blackbox"), dict) else {}
    core._header(draw, "BLACKBOX", "REC" if state else "WAIT")
    if not box:
        draw.text((2, 22), "Noch keine Historie", font=core.FONT_SMALL, fill=255)
        return _finish(image)

    draw.text((2, 17), f"Events24 {core._as_int(box.get('events'))}", font=core.FONT_SMALL, fill=255)
    draw.text((72, 17), f"Net {core._as_int(box.get('internet_outages'))}", font=core.FONT_SMALL, fill=255)
    draw.text((2, 30), f"Svc {core._as_int(box.get('service_crashes'))}", font=core.FONT_SMALL, fill=255)
    draw.text((72, 30), f"Bot {core._as_int(box.get('bot_errors'))}", font=core.FONT_SMALL, fill=255)
    latest = box.get("latest") if isinstance(box.get("latest"), list) else []
    if latest and isinstance(latest[0], dict):
        title = str(latest[0].get("title") or "Ereignis")
        draw.text((2, 46), core._truncate(draw, title, core.FONT_TINY, 124), font=core.FONT_TINY, fill=255)
    else:
        draw.text((2, 46), "Keine Auffaelligkeit", font=core.FONT_TINY, fill=255)
    return _finish(image)


def _render_warnings() -> Image.Image:
    image, draw = _new_image()
    state = _intelligence_state()
    warnings = state.get("warnings") if isinstance(state.get("warnings"), dict) else {}
    if not warnings.get("configured"):
        core._header(draw, "WARNZENTRALE", "CONFIG")
        draw.text((2, 18), "Region fehlt", font=core.FONT_MEDIUM, fill=255)
        draw.text((2, 38), ".env.homepi", font=core.FONT_SMALL, fill=255)
        draw.text((2, 50), "HOMEPI_WARNING_ARS", font=core.FONT_TINY, fill=255)
        return _finish(image)

    items = warnings.get("items") if isinstance(warnings.get("items"), list) else []
    active = len(items)
    core._header(draw, "WARNZENTRALE", f"{active} AKTIV")
    if not items:
        draw.text((2, 21), "KEINE WARNUNGEN", font=core.FONT_MEDIUM, fill=255)
        if warnings.get("error"):
            draw.text((2, 44), "Quelle temporaer offline", font=core.FONT_TINY, fill=255)
        else:
            draw.text((2, 44), "Region ohne Meldung", font=core.FONT_TINY, fill=255)
        return _finish(image)

    top = items[0] if isinstance(items[0], dict) else {}
    level = str(top.get("severity_label") or top.get("severity") or "WARN")
    provider = str(top.get("provider") or "NINA")
    draw.text((2, 17), core._truncate(draw, f"{level} / {provider}", core.FONT_TINY, 124), font=core.FONT_TINY, fill=255)
    for idx, line in enumerate(core._wrap(draw, str(top.get("headline") or "Amtliche Warnung"), core.FONT_SMALL, 124, 3)):
        draw.text((2, 28 + idx * 11), line, font=core.FONT_SMALL, fill=255)
    return _finish(image)


def _render_home_page(page: str, snap: core.HomeSnapshot) -> Image.Image:
    if page == "score":
        return _render_score()
    if page == "blackbox":
        return _render_blackbox()
    if page == "warnings":
        return _render_warnings()
    return _base_render_home_page(page, snap)


def _warning_overlay(self: core.Display2Service) -> tuple[str, str] | None:
    state = _intelligence_state()
    warnings = state.get("warnings") if isinstance(state.get("warnings"), dict) else {}
    items = warnings.get("items") if isinstance(warnings.get("items"), list) else []
    threshold = core._as_int(warnings.get("alert_threshold_rank")) or 3
    top = items[0] if items and isinstance(items[0], dict) else None
    if top and core._as_int(top.get("severity_rank")) >= threshold:
        key = f"{top.get('id')}|{top.get('headline')}"
        now = time.monotonic()
        if key != getattr(self, "_warning_alert_key", ""):
            self._warning_alert_key = key
            self._warning_alert_at = now
        if now - float(getattr(self, "_warning_alert_at", now)) <= core.ALERT_SECONDS:
            return ("AMTLICHE WARNUNG", str(top.get("headline") or "Warnzentrale"))
    else:
        self._warning_alert_key = ""
    return _base_alert(self)


def _page(self: core.Display2Service, now: float) -> str:
    if self._alert():
        return "alert"
    if self.voice.created_at and time.time() - self.voice.created_at <= core.VOICE_SECONDS:
        return "voice"
    if self.mesh.last_message_text and time.time() - self.mesh.last_message_at <= core.MESSAGE_SECONDS:
        return "message"

    pages = (core.MESH_PAGES + INTELLIGENCE_PAGES) if self.mesh.connected else core.HOME_PAGES
    profile = "mesh" if self.mesh.connected else "homepi"
    if profile != self.profile:
        self.profile = profile
        self.page_index = 0
        self.last_page_switch = now
    if self.last_page_switch == 0:
        self.last_page_switch = now
    elif now - self.last_page_switch >= core.PAGE_SECONDS:
        self.page_index = (self.page_index + 1) % len(pages)
        self.last_page_switch = now
    return pages[self.page_index % len(pages)]


def _render(self: core.Display2Service) -> Image.Image:
    if self.current_page == "message":
        return core.render_message(self.mesh)
    if self.current_page == "voice":
        return core.render_voice(self.voice)
    if self.current_page == "alert":
        return core.render_alert(*(self._alert() or ("HOMEPI", "Status geaendert")))
    if self.current_page in INTELLIGENCE_PAGES:
        return _render_home_page(self.current_page, self.home)
    if self.current_page in core.MESH_PAGES:
        return core.render_mesh_page(self.current_page, self.mesh)
    return _render_home_page(self.current_page, self.home)


_base_render_home_page = core.render_home_page

# Runtime policy for Display 2. Keep user overrides working, while making the
# default rotation a little slower and adding the three intelligence pages.
core.PAGE_SECONDS = max(2, min(30, int(os.getenv("DISPLAY2_PAGE_SECONDS", "8"))))
core.HOME_PAGES = core.HOME_PAGES + INTELLIGENCE_PAGES
core.PAGES = core.MESH_PAGES + core.HOME_PAGES
core._read_discord_stats = _read_discord_stats
core.render_home_page = _render_home_page
core.Display2Service._alert = _warning_overlay
core.Display2Service._page = _page
core.Display2Service._render = _render


def main() -> None:
    core.main()


if __name__ == "__main__":
    main()
