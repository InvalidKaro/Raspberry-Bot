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

MOBILE_PATCH = r"""
<style id="mobile-hotfix">
.detail-close{
  position:absolute;right:12px;top:12px;z-index:5;width:38px;height:38px;
  display:grid;place-items:center;border:1px solid rgba(120,255,210,.22);
  background:rgba(3,12,10,.88);color:#ecfff8;border-radius:12px;
  font:600 22px/1 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
  cursor:pointer;box-shadow:0 8px 30px rgba(0,0,0,.32);backdrop-filter:blur(10px)
}
.detail-close:hover{background:#78ffd2;color:#06100e;border-color:#78ffd2}
.detailhero{position:relative;padding-right:62px!important}

@media(max-width:900px){
  .topbar{
    left:8px!important;right:8px!important;top:max(8px,env(safe-area-inset-top))!important;
    height:58px!important;padding:0 11px!important;border-radius:15px!important;gap:8px!important
  }
  .logo{width:34px!important;height:34px!important;font-size:16px!important}
  .brand{gap:9px!important;min-width:0}.brand h1{font-size:14px!important;white-space:nowrap}
  .eyebrow{display:none!important}
  .statusrow{gap:5px!important;flex-wrap:nowrap!important}.statusrow .pill{display:none!important}
  .statusrow .pill:first-child{display:block!important;padding:7px 9px!important;font-size:10px!important}

  .side{
    left:8px!important;right:8px!important;bottom:max(8px,env(safe-area-inset-bottom))!important;
    top:auto!important;width:auto!important;height:min(32dvh,250px)!important;
    display:flex!important;flex-direction:column!important;gap:7px!important
  }
  .stats{
    display:grid!important;grid-template-columns:repeat(4,minmax(0,1fr))!important;
    flex:0 0 auto!important;border-radius:14px!important
  }
  .stat{
    min-width:0!important;padding:8px 6px!important;border-bottom:0!important;
    border-right:1px solid rgba(116,255,211,.10)!important;text-align:center
  }
  .stat:last-child{border-right:0!important}.stat span{font-size:8px!important;letter-spacing:.06em!important}
  .stat strong{font-size:15px!important;margin-top:3px!important;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .controls{display:none!important}
  .listcard{height:auto!important;min-height:0!important;flex:1 1 auto!important;border-radius:14px!important}
  .listhead{padding:9px 11px 7px!important}.listhead h2{font-size:10px!important}.listhead .pill{padding:5px 7px!important;font-size:9px!important}
  .airrow{padding:9px 11px!important}.callsign{font-size:12px!important}.type{font-size:10px!important}.airmeta{font-size:9px!important;gap:7px!important}

  .detail{
    left:8px!important;right:8px!important;bottom:max(8px,env(safe-area-inset-bottom))!important;
    top:auto!important;width:auto!important;max-height:calc(100dvh - 84px - env(safe-area-inset-top))!important;
    border-radius:18px!important;z-index:1300!important;overscroll-behavior:contain
  }
  .detailhero{padding:15px 58px 13px 15px!important}.detailhero h2{font-size:22px!important;margin-top:5px!important}
  .detailhero .planeicon{font-size:22px!important}.grid{grid-template-columns:1fr 1fr!important}
  .kv{padding:10px 11px!important}.kv span{font-size:8px!important}.kv strong{font-size:12px!important}
  .detailfoot{padding:11px!important}
  .detail-close{position:sticky;float:right;top:10px;margin:10px 10px -48px 0;width:40px;height:40px;border-radius:13px}

  body.detail-open .side{opacity:0!important;pointer-events:none!important;transform:translateY(12px);transition:.18s}
  .side{transition:.18s}
  .leaflet-bottom{bottom:calc(min(32dvh,250px) + 12px)!important}
  body.detail-open .leaflet-bottom{bottom:12px!important}
  .leaflet-control-zoom a{width:34px!important;height:34px!important;line-height:34px!important}
  .radar-overlay{width:92vw!important;max-width:560px}
  .plane-label{display:none!important}
  .toast{bottom:calc(min(32dvh,250px) + 18px)!important;max-width:calc(100vw - 28px);text-align:center}
  body.detail-open .toast{bottom:18px!important}
}

@media(max-width:420px){
  .side{height:min(30dvh,220px)!important}
  .leaflet-bottom{bottom:calc(min(30dvh,220px) + 12px)!important}
  .toast{bottom:calc(min(30dvh,220px) + 18px)!important}
  .stat span{font-size:7px!important}.stat strong{font-size:14px!important}
}
</style>
<script id="mobile-hotfix-js">
(() => {
  const detail = document.getElementById('detail');
  if (!detail) return;

  if (!document.getElementById('detailClose')) {
    const button = document.createElement('button');
    button.id = 'detailClose';
    button.className = 'detail-close';
    button.type = 'button';
    button.setAttribute('aria-label', 'Flugzeugdetails schließen');
    button.setAttribute('title', 'Schließen');
    button.textContent = '×';
    detail.prepend(button);
  }

  const syncDetailState = () => {
    document.body.classList.toggle('detail-open', detail.classList.contains('show'));
  };

  const closeDetail = () => {
    detail.classList.remove('show');
    document.body.classList.remove('detail-open');
    document.querySelectorAll('.airrow.active').forEach(el => el.classList.remove('active'));
    try { selected = null; } catch (_) {}
  };

  document.getElementById('detailClose')?.addEventListener('click', (event) => {
    event.preventDefault();
    event.stopPropagation();
    closeDetail();
  });

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') closeDetail();
  });

  new MutationObserver(syncDetailState).observe(detail, {attributes:true, attributeFilter:['class']});
  syncDetailState();
})();
</script>
"""

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
            except Exception as exc:
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
    html = TEMPLATE.read_text(encoding="utf-8")
    html = html.replace("</body>", MOBILE_PATCH + "\n</body>")
    return web.Response(
        text=html,
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
