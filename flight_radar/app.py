from __future__ import annotations

import asyncio
import math
import time
from pathlib import Path
from typing import Any

from aiohttp import ClientSession, ClientTimeout, web

BASE_DIR = Path(__file__).resolve().parent
TEMPLATE = BASE_DIR / "templates" / "index.html"

PRIMARY = "https://api.adsb.lol/v2/point/{lat}/{lon}/{radius}"
FALLBACK = "https://api.airplanes.live/v2/point/{lat}/{lon}/{radius}"
CACHE_TTL = 4.0
MAX_RADIUS_NM = 250.0

_cache: dict[tuple[float, float, float], tuple[float, dict[str, Any]]] = {}
_cache_lock = asyncio.Lock()


def _num(value: str | None, default: float, low: float, high: float) -> float:
    try:
        parsed = float(value) if value is not None else default
    except (TypeError, ValueError):
        parsed = default
    if not math.isfinite(parsed):
        parsed = default
    return max(low, min(high, parsed))


def _clean_aircraft(raw: dict[str, Any]) -> dict[str, Any] | None:
    lat = raw.get("lat")
    lon = raw.get("lon")
    if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
        return None

    return {
        "hex": str(raw.get("hex") or "").strip(),
        "flight": str(raw.get("flight") or "").strip(),
        "registration": raw.get("r"),
        "type": raw.get("t"),
        "description": raw.get("desc"),
        "lat": lat,
        "lon": lon,
        "alt_baro": raw.get("alt_baro"),
        "alt_geom": raw.get("alt_geom"),
        "ground_speed": raw.get("gs"),
        "track": raw.get("track"),
        "true_heading": raw.get("true_heading"),
        "mag_heading": raw.get("mag_heading"),
        "baro_rate": raw.get("baro_rate"),
        "geom_rate": raw.get("geom_rate"),
        "squawk": raw.get("squawk"),
        "category": raw.get("category"),
        "emergency": raw.get("emergency"),
        "nav_altitude_mcp": raw.get("nav_altitude_mcp"),
        "nav_altitude_fms": raw.get("nav_altitude_fms"),
        "nav_heading": raw.get("nav_heading"),
        "nav_modes": raw.get("nav_modes"),
        "mach": raw.get("mach"),
        "ias": raw.get("ias"),
        "tas": raw.get("tas"),
        "oat": raw.get("oat"),
        "tat": raw.get("tat"),
        "wind_speed": raw.get("wd"),
        "wind_dir": raw.get("ws"),
        "messages": raw.get("messages"),
        "seen": raw.get("seen"),
        "seen_pos": raw.get("seen_pos"),
        "rssi": raw.get("rssi"),
        "source": raw.get("type") or raw.get("source"),
        "db_flags": raw.get("dbFlags"),
    }


async def _fetch(session: ClientSession, url: str) -> dict[str, Any]:
    async with session.get(url, headers={"User-Agent": "HomePi-FlightRadar/1.0"}) as response:
        response.raise_for_status()
        return await response.json(content_type=None)


async def _aircraft_snapshot(lat: float, lon: float, radius: float) -> dict[str, Any]:
    key = (round(lat, 2), round(lon, 2), round(radius, 0))
    now = time.monotonic()

    async with _cache_lock:
        cached = _cache.get(key)
        if cached and now - cached[0] < CACHE_TTL:
            return {**cached[1], "cached": True}

    timeout = ClientTimeout(total=8)
    errors: list[str] = []
    source = "adsb.lol"
    payload: dict[str, Any] | None = None

    async with ClientSession(timeout=timeout) as session:
        for name, template in (("adsb.lol", PRIMARY), ("airplanes.live", FALLBACK)):
            url = template.format(lat=f"{lat:.5f}", lon=f"{lon:.5f}", radius=f"{radius:.0f}")
            try:
                payload = await _fetch(session, url)
                source = name
                break
            except Exception as exc:  # upstream/network errors are expected occasionally
                errors.append(f"{name}: {type(exc).__name__}")

    if payload is None:
        raise web.HTTPBadGateway(text="; ".join(errors) or "No aircraft data source available")

    aircraft: list[dict[str, Any]] = []
    for row in payload.get("ac", []):
        if isinstance(row, dict):
            cleaned = _clean_aircraft(row)
            if cleaned:
                aircraft.append(cleaned)

    result = {
        "ok": True,
        "source": source,
        "aircraft": aircraft,
        "count": len(aircraft),
        "center": {"lat": lat, "lon": lon},
        "radius_nm": radius,
        "generated_at": int(time.time()),
        "cached": False,
    }

    async with _cache_lock:
        _cache[key] = (now, result)
        if len(_cache) > 64:
            oldest = min(_cache.items(), key=lambda item: item[1][0])[0]
            _cache.pop(oldest, None)

    return result


async def index(_: web.Request) -> web.Response:
    return web.Response(
        text=TEMPLATE.read_text(encoding="utf-8"),
        content_type="text/html",
        headers={"Cache-Control": "no-store"},
    )


async def api_aircraft(request: web.Request) -> web.Response:
    lat = _num(request.query.get("lat"), 49.8917, -90.0, 90.0)
    lon = _num(request.query.get("lon"), 10.8868, -180.0, 180.0)
    radius = _num(request.query.get("radius"), 120.0, 10.0, MAX_RADIUS_NM)

    try:
        result = await _aircraft_snapshot(lat, lon, radius)
    except web.HTTPException as exc:
        return web.json_response({"ok": False, "message": exc.text}, status=exc.status)
    return web.json_response(result, headers={"Cache-Control": "no-store"})


async def health(_: web.Request) -> web.Response:
    return web.json_response({"ok": True, "service": "homepi-flight-radar"})


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/api/aircraft", api_aircraft)
    app.router.add_get("/health", health)
    return app


def main() -> None:
    web.run_app(create_app(), host="0.0.0.0", port=8093, print=None, access_log=None)


if __name__ == "__main__":
    main()
