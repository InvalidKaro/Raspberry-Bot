from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from PIL import Image, ImageDraw

from display_service import main_base as _core
from display_service.main_base import *  # noqa: F403

_RADIO_CACHE: dict[str, Any] = {}
_RADIO_CACHE_AT = 0.0
_RADIO_CACHE_TTL = 4.0
_SPOTIFY_CACHE: dict[str, Any] = {}
_SPOTIFY_CACHE_AT = 0.0
_SPOTIFY_CACHE_TTL = 4.0
_ORIGINAL_RENDER_IMAGE = _core.render_image
_ORIGINAL_MAYBE_REFRESH = _core.DisplayService.maybe_refresh


def _runtime_voice(con: sqlite3.Connection) -> dict[str, Any]:
    try:
        runtime_row = con.execute(
            "SELECT state_json FROM dashboard_runtime_state WHERE guild_id=?",
            (_core.GUILD_ID,),
        ).fetchone()
    except sqlite3.Error:
        return {}
    if not runtime_row:
        return {}
    try:
        raw = json.loads(runtime_row["state_json"] or "{}")
    except (ValueError, TypeError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    voice = raw.get("voice")
    return voice if isinstance(voice, dict) else {}


def _read_radio_state() -> dict[str, Any]:
    global _RADIO_CACHE, _RADIO_CACHE_AT
    now = time.monotonic()
    if now - _RADIO_CACHE_AT < _RADIO_CACHE_TTL:
        return _RADIO_CACHE

    radio: dict[str, Any] = {
        "active": False,
        "station_name": "",
        "stream_title": "",
        "artist": "",
        "track": "",
        "genre": "",
        "bitrate_kbps": None,
        "codec": "",
        "volume": None,
        "elapsed_seconds": 0,
        "paused": False,
    }

    try:
        with _core._connect() as con:
            try:
                row = con.execute(
                    """
                    SELECT active,station_name,stream_title,artist,track,genre,
                           bitrate_kbps,codec,metadata_supported,last_error
                    FROM radio_runtime_metadata WHERE guild_id=?
                    """,
                    (_core.GUILD_ID,),
                ).fetchone()
            except sqlite3.Error:
                row = None
            if row:
                radio.update(dict(row))
            voice = _runtime_voice(con)

        is_radio = str(voice.get("kind") or "").lower() == "radio"
        voice_active = bool(is_radio and (voice.get("playing") or voice.get("paused")))
        radio["active"] = bool(voice_active)
        if voice_active:
            radio["station_name"] = str(voice.get("title") or radio.get("station_name") or "")
        radio["volume"] = voice.get("volume")
        radio["elapsed_seconds"] = int(voice.get("elapsed_seconds") or 0)
        radio["paused"] = bool(voice.get("paused"))
    except (sqlite3.Error, ValueError, TypeError, OSError):
        radio["active"] = False

    _RADIO_CACHE = radio
    _RADIO_CACHE_AT = now
    return radio


def _read_spotify_state() -> dict[str, Any]:
    global _SPOTIFY_CACHE, _SPOTIFY_CACHE_AT
    now = time.monotonic()
    if now - _SPOTIFY_CACHE_AT < _SPOTIFY_CACHE_TTL:
        return _SPOTIFY_CACHE

    spotify: dict[str, Any] = {
        "active": False,
        "current": None,
        "queue": [],
        "volume": None,
        "elapsed_seconds": 0,
        "paused": False,
    }
    try:
        with _core._connect() as con:
            try:
                row = con.execute(
                    "SELECT state_json FROM spotify_runtime_state WHERE guild_id=?",
                    (_core.GUILD_ID,),
                ).fetchone()
            except sqlite3.Error:
                row = None
            if row:
                try:
                    parsed = json.loads(row["state_json"] or "{}")
                    if isinstance(parsed, dict):
                        spotify.update(parsed)
                except (ValueError, TypeError, json.JSONDecodeError):
                    pass
            voice = _runtime_voice(con)

        is_spotify = str(voice.get("kind") or "").lower() == "spotify"
        spotify["active"] = bool(is_spotify and (voice.get("playing") or voice.get("paused")))
        spotify["volume"] = voice.get("volume") if is_spotify else None
        spotify["elapsed_seconds"] = int(voice.get("elapsed_seconds") or 0) if is_spotify else 0
        spotify["paused"] = bool(voice.get("paused")) if is_spotify else False
    except (sqlite3.Error, ValueError, TypeError, OSError):
        spotify["active"] = False

    _SPOTIFY_CACHE = spotify
    _SPOTIFY_CACHE_AT = now
    return spotify


def _wrap_lines(draw: ImageDraw.ImageDraw, value: str, font, width: int, max_lines: int) -> list[str]:
    words = " ".join(str(value or "").split()).split()
    if not words:
        return []
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if draw.textbbox((0, 0), candidate, font=font)[2] <= width:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = word
        if len(lines) >= max_lines:
            break
    if current and len(lines) < max_lines:
        lines.append(current)
    return [_core._truncate(draw, line, font, width) for line in lines[:max_lines]]


def _elapsed_short(seconds: int) -> str:
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _render_radio_page(radio: dict[str, Any], layout: dict[str, Any]) -> Image.Image:
    image = Image.new("1", (128, 64), 0)
    draw = ImageDraw.Draw(image)

    station = str(radio.get("station_name") or radio.get("stream_name") or "RADIO")
    status = "PAUSE" if radio.get("paused") else "LIVE"
    draw.text((2, 2), _core._truncate(draw, station, _core.FONT_SMALL, 91), font=_core.FONT_SMALL, fill=255)
    status_width = draw.textbbox((0, 0), status, font=_core.FONT_TINY)[2]
    draw.text((126 - status_width, 3), status, font=_core.FONT_TINY, fill=255)
    draw.line((0, 13, 127, 13), fill=255)

    artist = str(radio.get("artist") or "").strip()
    track = str(radio.get("track") or "").strip()
    stream_title = str(radio.get("stream_title") or "").strip()

    y = 17
    if artist and track:
        draw.text((2, y), _core._truncate(draw, artist, _core.FONT_TINY, 124), font=_core.FONT_TINY, fill=255)
        y += 10
        track_lines = _wrap_lines(draw, track, _core.FONT_SMALL, 124, 2)
        for line in track_lines:
            draw.text((2, y), line, font=_core.FONT_SMALL, fill=255)
            y += 11
    elif stream_title:
        for line in _wrap_lines(draw, stream_title, _core.FONT_SMALL, 124, 3):
            draw.text((2, y), line, font=_core.FONT_SMALL, fill=255)
            y += 11
    else:
        genre = str(radio.get("genre") or "Live-Stream").strip() or "Live-Stream"
        draw.text((2, 22), _core._truncate(draw, genre, _core.FONT_SMALL, 124), font=_core.FONT_SMALL, fill=255)
        draw.text((2, 35), "Keine Song-Metadaten", font=_core.FONT_TINY, fill=255)

    volume = radio.get("volume")
    elapsed = _elapsed_short(int(radio.get("elapsed_seconds") or 0))
    left = f"VOL {int(volume)}%" if volume is not None else "RADIO"
    draw.text((2, 47), left, font=_core.FONT_TINY, fill=255)
    elapsed_width = draw.textbbox((0, 0), elapsed, font=_core.FONT_TINY)[2]
    draw.text((126 - elapsed_width, 47), elapsed, font=_core.FONT_TINY, fill=255)

    if layout["show_footer"]:
        draw.line((0, 55, 127, 55), fill=255)
        stream_parts = []
        bitrate = radio.get("bitrate_kbps")
        if bitrate:
            stream_parts.append(f"{int(bitrate)}k")
        codec = str(radio.get("codec") or "").strip()
        if codec:
            stream_parts.append(codec)
        footer = " · ".join(stream_parts) or "NOW PLAYING"
        draw.text((2, 57), _core._truncate(draw, footer, _core.FONT_TINY, 92), font=_core.FONT_TINY, fill=255)
        draw.text((103, 57), "HP", font=_core.FONT_TINY, fill=255)

    if layout["rotation"] == 180:
        image = image.rotate(180)
    return image


def _render_spotify_page(spotify: dict[str, Any], layout: dict[str, Any]) -> Image.Image:
    image = Image.new("1", (128, 64), 0)
    draw = ImageDraw.Draw(image)
    current = spotify.get("current") if isinstance(spotify.get("current"), dict) else {}
    artist = str(current.get("artist") or "Spotify").strip()
    title = str(current.get("title") or "Unbekannter Titel").strip()
    status = "PAUSE" if spotify.get("paused") else "PLAY"

    draw.text((2, 2), "SPOTIFY", font=_core.FONT_SMALL, fill=255)
    status_width = draw.textbbox((0, 0), status, font=_core.FONT_TINY)[2]
    draw.text((126 - status_width, 3), status, font=_core.FONT_TINY, fill=255)
    draw.line((0, 13, 127, 13), fill=255)

    draw.text((2, 17), _core._truncate(draw, artist, _core.FONT_TINY, 124), font=_core.FONT_TINY, fill=255)
    y = 27
    for line in _wrap_lines(draw, title, _core.FONT_SMALL, 124, 2):
        draw.text((2, y), line, font=_core.FONT_SMALL, fill=255)
        y += 11

    volume = spotify.get("volume")
    elapsed = _elapsed_short(int(spotify.get("elapsed_seconds") or 0))
    left = f"VOL {int(volume)}%" if volume is not None else "SPOTIFY"
    draw.text((2, 47), left, font=_core.FONT_TINY, fill=255)
    elapsed_width = draw.textbbox((0, 0), elapsed, font=_core.FONT_TINY)[2]
    draw.text((126 - elapsed_width, 47), elapsed, font=_core.FONT_TINY, fill=255)

    if layout["show_footer"]:
        draw.line((0, 55, 127, 55), fill=255)
        queue = spotify.get("queue") if isinstance(spotify.get("queue"), list) else []
        draw.text((2, 57), f"QUEUE {len(queue)}", font=_core.FONT_TINY, fill=255)
        draw.text((103, 57), "HP", font=_core.FONT_TINY, fill=255)

    if layout["rotation"] == 180:
        image = image.rotate(180)
    return image


def render_image(page: str, snap: Snapshot, layout: dict[str, Any]) -> Image.Image:  # noqa: F405
    if page == "media":
        radio = _read_radio_state()
        if radio.get("active"):
            return _render_radio_page(radio, layout)
        spotify = _read_spotify_state()
        if spotify.get("active"):
            return _render_spotify_page(spotify, layout)
    return _ORIGINAL_RENDER_IMAGE(page, snap, layout)


def _maybe_refresh_with_media_priority(self, now: float) -> None:
    _ORIGINAL_MAYBE_REFRESH(self, now)
    radio = _read_radio_state()
    spotify = _read_spotify_state()
    if radio.get("active"):
        key = "radio|" + "|".join(
            (
                str(radio.get("station_name") or ""),
                str(radio.get("stream_title") or radio.get("track") or ""),
            )
        )
    elif spotify.get("active"):
        current = spotify.get("current") if isinstance(spotify.get("current"), dict) else {}
        key = "spotify|" + "|".join((str(current.get("artist") or ""), str(current.get("title") or "")))
    else:
        self._media_metadata_key = ""
        return

    previous = getattr(self, "_media_metadata_key", "")
    self._media_metadata_key = key
    if key and key != previous and self.layout.get("media_priority", True):
        self.priority_page = "media"
        self.priority_until = now + self.layout["page_seconds"]


_core.render_image = render_image
_core.DisplayService.maybe_refresh = _maybe_refresh_with_media_priority

# Re-export the patched objects for the existing smoke test/import contract.
DisplayService = _core.DisplayService
DEFAULT_LAYOUT = _core.DEFAULT_LAYOUT
PAGES = _core.PAGES
Snapshot = _core.Snapshot
check = _core.check


def main() -> None:
    _core.main()


if __name__ == "__main__":
    main()