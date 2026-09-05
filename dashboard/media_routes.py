from __future__ import annotations

import asyncio
import json
import sqlite3
from typing import Any

from aiohttp import web

from dashboard import media_routes_base as _base

AMBIENT_CATALOG = _base.AMBIENT_CATALOG
TEMPLATE_DIR = _base.TEMPLATE_DIR

RADIO_METADATA_SCHEMA = """
CREATE TABLE IF NOT EXISTS radio_runtime_metadata(
    guild_id INTEGER PRIMARY KEY,
    active INTEGER NOT NULL DEFAULT 0,
    station_name TEXT,
    stream_title TEXT,
    artist TEXT,
    track TEXT,
    genre TEXT,
    homepage TEXT,
    stream_name TEXT,
    stream_genre TEXT,
    bitrate_kbps INTEGER,
    codec TEXT,
    content_type TEXT,
    metadata_supported INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

SPOTIFY_RUNTIME_SCHEMA = """
CREATE TABLE IF NOT EXISTS spotify_runtime_state(
    guild_id INTEGER PRIMARY KEY,
    state_json TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

_RADIO_CSS = r"""
<style>
.radio-live-card{border-color:#203d52;background:linear-gradient(145deg,rgba(11,25,37,.98),rgba(17,21,28,.96))}
.radio-live-head{display:flex;align-items:center;justify-content:space-between;gap:10px}
.radio-live-badge{display:inline-flex;align-items:center;gap:6px;padding:4px 8px;border-radius:999px;background:#15202b;border:1px solid #294255;color:#9eabb9;font-size:10px;font-weight:800;letter-spacing:.08em}
.radio-live-badge.on{background:#3b1017;border-color:#7d2634;color:#ffb7c0}.radio-live-badge.on::before{content:'';width:7px;height:7px;border-radius:50%;background:#ff4057;box-shadow:0 0 10px #ff4057}
.radio-live-station{font-size:18px;font-weight:850;letter-spacing:-.02em;margin-bottom:10px}.radio-live-artist{color:#91d5ff;font-weight:800;font-size:13px}.radio-live-track{font-size:16px;font-weight:800;margin-top:2px;overflow-wrap:anywhere}
.radio-live-meta{display:flex;gap:6px;flex-wrap:wrap;margin-top:12px}.radio-live-chip{padding:5px 8px;border-radius:999px;background:#0b1118;border:1px solid #243240;color:#b7c4d1;font-size:11px}.radio-live-muted{color:var(--muted);font-size:12px}.radio-live-error{margin-top:9px;color:#d89ca3;font-size:10px}
.spotify-card{border-color:#1f5434;background:linear-gradient(145deg,rgba(10,29,18,.98),rgba(17,21,28,.96))}.spotify-head{display:flex;align-items:center;justify-content:space-between;gap:10px}.spotify-badge{display:inline-flex;align-items:center;gap:6px;padding:4px 8px;border-radius:999px;background:#122018;border:1px solid #28543a;color:#8dbaa0;font-size:10px;font-weight:800;letter-spacing:.08em}.spotify-badge.on{background:#10351f;border-color:#1db954;color:#b9f5cd}.spotify-badge.on::before{content:'';width:7px;height:7px;border-radius:50%;background:#1ed760;box-shadow:0 0 10px #1ed760}.spotify-title{font-size:16px;font-weight:850;overflow-wrap:anywhere}.spotify-artist{color:#77e69d;font-size:13px;font-weight:800;margin-top:2px}.spotify-album{color:var(--muted);font-size:11px;margin-top:3px}.spotify-meta{display:flex;gap:6px;flex-wrap:wrap;margin-top:10px}.spotify-chip{padding:5px 8px;border-radius:999px;background:#0b1510;border:1px solid #24412e;color:#b7d7c2;font-size:11px}.spotify-control-row{display:grid;grid-template-columns:1fr auto auto;gap:8px;margin-top:12px}.spotify-control-row button{white-space:nowrap}.spotify-play{background:#1db954}.spotify-add{background:#176f39}@media(max-width:600px){.spotify-control-row{grid-template-columns:1fr 1fr}.spotify-control-row input{grid-column:1/-1}}
</style>
"""

_RADIO_SCRIPT = r"""
<script>
(()=>{
  const aside=document.querySelector('aside');
  if(!aside||document.querySelector('#radioLiveCard'))return;
  const card=document.createElement('section');
  card.className='card radio-live-card';card.id='radioLiveCard';
  card.innerHTML='<div class="head"><div class="radio-live-head"><h2>📻 Radio Live</h2></div><span id="radioLiveBadge" class="radio-live-badge">OFFLINE</span></div><div class="body"><div id="radioLiveBody" class="radio-live-muted">Kein Live-Radio aktiv.</div></div>';
  aside.prepend(card);

  const h=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const duration=s=>{s=Math.max(0,Number(s)||0);const hr=Math.floor(s/3600),min=Math.floor((s%3600)/60),sec=Math.floor(s%60);return hr?`${hr}:${String(min).padStart(2,'0')}:${String(sec).padStart(2,'0')}`:`${min}:${String(sec).padStart(2,'0')}`};
  function renderRadioLive(radio){
    const badge=document.querySelector('#radioLiveBadge'),body=document.querySelector('#radioLiveBody');
    if(!badge||!body)return;
    if(!radio||!radio.active){badge.className='radio-live-badge';badge.textContent='OFFLINE';body.className='radio-live-muted';body.innerHTML='Kein Live-Radio aktiv.';return;}
    badge.className='radio-live-badge on';badge.textContent=radio.paused?'PAUSE':'LIVE';body.className='';
    const station=h(radio.station_name||radio.stream_name||'Radio');
    const artist=h(radio.artist||'');const track=h(radio.track||'');const raw=h(radio.stream_title||'');
    let now='';
    if(artist&&track)now=`<div class="radio-live-artist">${artist}</div><div class="radio-live-track">${track}</div>`;
    else if(raw)now=`<div class="radio-live-track">${raw}</div>`;
    else now='<div class="radio-live-muted">Der Stream liefert aktuell keine Song-Metadaten.</div>';
    const chips=[];
    if(radio.genre)chips.push(h(radio.genre));
    if(radio.volume!=null)chips.push(`🔊 ${h(radio.volume)}%`);
    if(radio.elapsed_seconds!=null)chips.push(`⏱ ${duration(radio.elapsed_seconds)}`);
    if(radio.channel_name)chips.push(`Voice · ${h(radio.channel_name)}`);
    const stream=[radio.bitrate_kbps?`${h(radio.bitrate_kbps)} kbps`:'',radio.codec?h(radio.codec):''].filter(Boolean).join(' · ');if(stream)chips.push(stream);
    body.innerHTML=`<div class="radio-live-station">${station}</div>${now}<div class="radio-live-meta">${chips.map(x=>`<span class="radio-live-chip">${x}</span>`).join('')}</div>${radio.last_error&&!radio.stream_title?`<div class="radio-live-error">Metadaten vorübergehend nicht verfügbar.</div>`:''}`;
  }
  async function refreshRadioLive(){
    const sel=document.querySelector('#guild');const gid=sel?.value||'';
    if(!gid){renderRadioLive(null);return;}
    try{const response=await fetch('/api/media/state?guild_id='+encodeURIComponent(gid),{cache:'no-store'});const data=await response.json();if(response.ok&&data.ok!==false)renderRadioLive(data.radio||null)}catch(_e){}
  }
  document.querySelector('#guild')?.addEventListener('change',()=>setTimeout(refreshRadioLive,150));
  document.querySelector('#refresh')?.addEventListener('click',()=>setTimeout(refreshRadioLive,250));
  setInterval(refreshRadioLive,10000);setTimeout(refreshRadioLive,500);
})();
</script>
"""

_SPOTIFY_SCRIPT = r"""
<script>
(()=>{
  if(document.querySelector('#spotifyMediaCard'))return;
  const radioGrid=document.querySelector('#stationGrid');
  const radioSection=radioGrid?.closest('section.card');
  if(!radioSection)return;
  const section=document.createElement('section');
  section.className='card section-gap spotify-card';section.id='spotifyMediaCard';
  section.innerHTML=`<div class="head"><div class="spotify-head"><h2>🟢 Spotify</h2></div><span id="spotifyBadge" class="spotify-badge">READY</span></div><div class="body"><div id="spotifyLive"><div class="radio-live-muted">Spotify-Link einfügen oder – mit API-Zugang – nach einem Titel suchen.</div></div><div class="spotify-control-row"><input id="spotifySource" placeholder="Spotify Track / Album / Playlist URL oder Suchbegriff"><button id="spotifyPlay" class="spotify-play">▶ Play</button><button id="spotifyAdd" class="spotify-add">＋ Queue</button></div><div id="spotifyHint" class="radio-live-muted" style="margin-top:8px"></div></div>`;
  radioSection.insertAdjacentElement('afterend',section);

  const h=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const duration=s=>{s=Math.max(0,Number(s)||0);const hr=Math.floor(s/3600),min=Math.floor((s%3600)/60),sec=Math.floor(s%60);return hr?`${hr}:${String(min).padStart(2,'0')}:${String(sec).padStart(2,'0')}`:`${min}:${String(sec).padStart(2,'0')}`};
  function renderSpotify(s){
    const badge=document.querySelector('#spotifyBadge'),body=document.querySelector('#spotifyLive'),hint=document.querySelector('#spotifyHint');
    if(!badge||!body)return;
    const active=!!s?.active;badge.className='spotify-badge'+(active?' on':'');badge.textContent=active?(s.paused?'PAUSE':'PLAYING'):'READY';
    const c=s?.current||null;const q=Array.isArray(s?.queue)?s.queue.length:Number(s?.queue_count||0);
    if(c){
      const chips=[];if(s.volume!=null)chips.push(`🔊 ${h(s.volume)}%`);if(s.elapsed_seconds!=null)chips.push(`⏱ ${duration(s.elapsed_seconds)}`);if(s.channel_name)chips.push(`Voice · ${h(s.channel_name)}`);chips.push(`Queue ${q}`);
      body.innerHTML=`<div class="spotify-title">${h(c.title||'Spotify')}</div>${c.artist?`<div class="spotify-artist">${h(c.artist)}</div>`:''}${c.album?`<div class="spotify-album">${h(c.album)}</div>`:''}<div class="spotify-meta">${chips.map(x=>`<span class="spotify-chip">${x}</span>`).join('')}</div>`;
    }else body.innerHTML='<div class="radio-live-muted">Kein Spotify-Titel aktiv. Tracklinks funktionieren direkt.</div>';
    if(hint)hint.textContent=s?.credentials_configured?'Spotify API aktiv: Suche, Album- und Playlist-Import verfügbar.':'Ohne Spotify-App: direkte Tracklinks funktionieren; Suche/Album/Playlist benötigen API-Zugangsdaten.';
  }
  async function refresh(){const gid=document.querySelector('#guild')?.value||'';if(!gid){renderSpotify(null);return;}try{const r=await fetch('/api/media/state?guild_id='+encodeURIComponent(gid),{cache:'no-store'});const d=await r.json();if(r.ok&&d.ok!==false)renderSpotify(d.spotify||null)}catch(_e){}}
  async function action(kind){
    const gid=document.querySelector('#guild')?.value||'',cid=document.querySelector('#channel')?.value||'',source=document.querySelector('#spotifySource')?.value?.trim()||'',volume=Math.max(10,Math.min(120,Number(document.querySelector('#volume')?.value)||65));
    if(!gid)return window.note?.('Server wählen.',false);if(!source)return window.note?.('Spotify-Link oder Suchbegriff eingeben.',false);if(kind==='spotify-play'&&!cid)return window.note?.('Voice-Channel wählen.',false);
    try{const data=await window.post('/api/media/action',{action:kind,guild_id:gid,channel_id:cid,volume,source});window.note?.(`Queue #${data.command_id}: ${data.message}`);setTimeout(refresh,2200)}catch(e){window.note?.(e.message,false)}
  }
  document.querySelector('#spotifyPlay')?.addEventListener('click',()=>action('spotify-play'));
  document.querySelector('#spotifyAdd')?.addEventListener('click',()=>action('spotify-add'));
  document.querySelector('#guild')?.addEventListener('change',()=>setTimeout(refresh,150));
  document.querySelector('#refresh')?.addEventListener('click',()=>setTimeout(refresh,250));
  setInterval(refresh,10000);setTimeout(refresh,650);
})();
</script>
"""


def _inject_media_extensions(html: str) -> str:
    if "spotify-card" not in html and "</head>" in html:
        html = html.replace("</head>", _RADIO_CSS + "\n</head>", 1)
    if "id=\"radioLiveCard\"" not in html and "</body>" in html:
        html = html.replace("</body>", _RADIO_SCRIPT + "\n" + _SPOTIFY_SCRIPT + "\n</body>", 1)
    return html


def _read_runtime_voice(con: sqlite3.Connection, guild_id: int) -> dict[str, Any]:
    try:
        runtime_row = con.execute(
            "SELECT state_json FROM dashboard_runtime_state WHERE guild_id=?",
            (guild_id,),
        ).fetchone()
    except sqlite3.Error:
        return {}
    if not runtime_row:
        return {}
    try:
        parsed = json.loads(runtime_row["state_json"] or "{}")
    except (ValueError, TypeError, json.JSONDecodeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    voice = parsed.get("voice")
    return voice if isinstance(voice, dict) else {}


def _read_radio_state(config: Any, guild_id: int) -> dict[str, Any]:
    con = _base._connect(config)
    try:
        con.execute(RADIO_METADATA_SCHEMA)
        con.commit()
        row = con.execute(
            """
            SELECT active,station_name,stream_title,artist,track,genre,homepage,
                   stream_name,stream_genre,bitrate_kbps,codec,content_type,
                   metadata_supported,last_error,updated_at
            FROM radio_runtime_metadata WHERE guild_id=?
            """,
            (guild_id,),
        ).fetchone()
        radio = dict(row) if row else {
            "active": 0,
            "station_name": "",
            "stream_title": "",
            "artist": "",
            "track": "",
            "genre": "",
            "homepage": "",
            "stream_name": "",
            "stream_genre": "",
            "bitrate_kbps": None,
            "codec": "",
            "content_type": "",
            "metadata_supported": 0,
            "last_error": "",
            "updated_at": None,
        }
        voice = _read_runtime_voice(con, guild_id)
        is_radio = str(voice.get("kind") or "").lower() == "radio"
        voice_active = bool(is_radio and (voice.get("playing") or voice.get("paused")))
        radio["active"] = bool(voice_active)
        if voice_active:
            radio["station_name"] = str(voice.get("title") or radio.get("station_name") or "")
        radio["paused"] = bool(voice.get("paused"))
        radio["playing"] = bool(voice.get("playing"))
        radio["volume"] = voice.get("volume")
        radio["elapsed_seconds"] = voice.get("elapsed_seconds")
        radio["channel_name"] = voice.get("channel_name")
        radio["metadata_supported"] = bool(radio.get("metadata_supported"))
        return radio
    finally:
        con.close()


def _read_spotify_state(config: Any, guild_id: int) -> dict[str, Any]:
    con = _base._connect(config)
    try:
        con.execute(SPOTIFY_RUNTIME_SCHEMA)
        con.commit()
        row = con.execute(
            "SELECT state_json,updated_at FROM spotify_runtime_state WHERE guild_id=?",
            (guild_id,),
        ).fetchone()
        spotify: dict[str, Any] = {
            "active": False,
            "credentials_configured": False,
            "current": None,
            "queue": [],
            "updated_at": None,
        }
        if row:
            try:
                parsed = json.loads(row["state_json"] or "{}")
                if isinstance(parsed, dict):
                    spotify.update(parsed)
            except (ValueError, TypeError, json.JSONDecodeError):
                pass
            spotify["updated_at"] = row["updated_at"]
        voice = _read_runtime_voice(con, guild_id)
        is_spotify = str(voice.get("kind") or "").lower() == "spotify"
        spotify["active"] = bool(is_spotify and (voice.get("playing") or voice.get("paused")))
        spotify["paused"] = bool(voice.get("paused")) if is_spotify else False
        spotify["playing"] = bool(voice.get("playing")) if is_spotify else False
        spotify["volume"] = voice.get("volume") if is_spotify else None
        spotify["elapsed_seconds"] = voice.get("elapsed_seconds") if is_spotify else 0
        spotify["channel_name"] = voice.get("channel_name") if is_spotify else None
        spotify["queue_count"] = len(spotify.get("queue") or []) if isinstance(spotify.get("queue"), list) else 0
        return spotify
    finally:
        con.close()


async def media_page(request: web.Request) -> web.Response:
    response = await _base.media_page(request)
    return web.Response(
        text=_inject_media_extensions(response.text),
        content_type="text/html",
        headers={"Cache-Control": "no-store"},
    )


async def api_media_state(request: web.Request) -> web.Response:
    base_response = await _base.api_media_state(request)
    if base_response.status >= 400:
        return base_response

    try:
        payload = json.loads(base_response.text)
        guild_id = int(payload.get("guild_id") or 0)
    except (TypeError, ValueError, json.JSONDecodeError):
        return base_response

    if guild_id <= 0:
        return base_response

    radio, spotify = await asyncio.gather(
        asyncio.to_thread(_read_radio_state, request.app["config"], guild_id),
        asyncio.to_thread(_read_spotify_state, request.app["config"], guild_id),
    )
    payload["radio"] = radio
    payload["spotify"] = spotify
    return web.json_response(payload)


async def api_media_action(request: web.Request) -> web.Response:
    try:
        data = await request.json()
    except (json.JSONDecodeError, ValueError):
        return web.json_response({"ok": False, "message": "Ungültige JSON-Anfrage."}, status=400)
    action = str(data.get("action", "")).strip().lower()
    if action not in {"spotify-play", "spotify-add"}:
        return await _base.api_media_action(request)

    try:
        guild_id = _base._guild_id(data.get("guild_id"))
        source = " ".join(str(data.get("source") or "").split()).strip()[:1000]
        if not source:
            raise ValueError("Spotify-Link oder Suchbegriff fehlt.")
        payload: dict[str, Any] = {"guild_id": str(guild_id), "source": source}
        if action == "spotify-play":
            payload["channel_id"] = str(_base._channel_id(data.get("channel_id")))
            payload["volume"] = _base._clamp(data.get("volume"), 10, 120, 65)
        else:
            payload["requested_by"] = 0
    except ValueError as exc:
        return web.json_response({"ok": False, "message": str(exc)}, status=400)

    command_id = await asyncio.to_thread(_base._enqueue, request.app["config"], f"media-{action}", payload)
    return web.json_response({"ok": True, "command_id": command_id, "message": "Spotify-Aktion an den Bot übergeben."})


api_media_station = _base.api_media_station
api_media_ambient_source = _base.api_media_ambient_source


def register_media_routes(app: web.Application) -> None:
    app.router.add_get("/media", media_page)
    app.router.add_get("/api/media/state", api_media_state)
    app.router.add_post("/api/media/station", api_media_station)
    app.router.add_post("/api/media/ambient-source", api_media_ambient_source)
    app.router.add_post("/api/media/action", api_media_action)