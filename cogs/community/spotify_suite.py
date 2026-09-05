from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import defaultdict, deque
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlparse

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

logger = logging.getLogger(__name__)

SPOTIFY_GREEN = 0x1DB954
DEFAULT_VOLUME = 65
MAX_QUEUE_PER_GUILD = 25
SPOTIFY_OEMBED_URL = "https://open.spotify.com/oembed"
SPOTIFY_API_BASE = "https://api.spotify.com/v1"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"

SPOTIFY_RUNTIME_SCHEMA = """
CREATE TABLE IF NOT EXISTS spotify_runtime_state(
    guild_id INTEGER PRIMARY KEY,
    state_json TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


@dataclass(slots=True)
class SpotifyTrack:
    spotify_url: str
    title: str
    artist: str
    album: str
    thumbnail: str
    requested_by: int
    spotify_id: str = ""
    duration_ms: int | None = None

    @property
    def display_title(self) -> str:
        return f"{self.artist} — {self.title}" if self.artist else self.title

    @property
    def duration_seconds(self) -> int | None:
        if self.duration_ms is None:
            return None
        return max(0, int(self.duration_ms) // 1000)

    @property
    def audio_query(self) -> str:
        if self.artist:
            return f"{self.artist} - {self.title} official audio"
        return f"{self.title} official audio"


class SpotifySuite(
    commands.GroupCog,
    group_name="spotify",
    group_description="Spotify-Links als Metadaten nutzen und passend über die Voice-Engine abspielen",
):
    """Spotify integration without attempting to extract Spotify's protected audio.

    Spotify provides metadata only. The actual Discord voice audio is resolved through
    the already configured YouTube/yt-dlp voice pipeline. This keeps the integration
    compatible with Spotify's normal public metadata surfaces while still allowing
    Spotify links to drive the existing HomePi media system.
    """

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.session: aiohttp.ClientSession | None = None
        self.queues: dict[int, deque[SpotifyTrack]] = defaultdict(deque)
        self.current: dict[int, SpotifyTrack] = {}
        self.session_active: set[int] = set()
        self.starting: set[int] = set()
        self._token = ""
        self._token_expires_at = 0.0
        self.queue_guard.start()

    async def cog_load(self) -> None:
        await self.bot.database.connection.executescript(SPOTIFY_RUNTIME_SCHEMA)
        await self.bot.database.connection.commit()
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=15, connect=5, sock_connect=5, sock_read=10)
        )

    async def cog_unload(self) -> None:
        self.queue_guard.cancel()
        if self.session is not None and not self.session.closed:
            await self.session.close()
        self.session = None

    def _client_credentials(self) -> tuple[str, str]:
        return (
            os.getenv("SPOTIFY_CLIENT_ID", "").strip(),
            os.getenv("SPOTIFY_CLIENT_SECRET", "").strip(),
        )

    def _credentials_configured(self) -> bool:
        client_id, client_secret = self._client_credentials()
        return bool(client_id and client_secret)

    def _youtube(self):
        youtube = self.bot.get_cog("YouTubeSuite")
        if youtube is None:
            raise RuntimeError("YouTubeSuite ist nicht geladen. Spotify benötigt die vorhandene Audio-Resolver-Pipeline.")
        return youtube

    def _voice(self):
        voice = self.bot.get_cog("VoiceSuite")
        if voice is None:
            raise RuntimeError("VoiceSuite ist nicht geladen.")
        return voice

    async def _require_queue_access(self, interaction: discord.Interaction) -> bool:
        if interaction.guild_id is None:
            return False
        youtube = self.bot.get_cog("YouTubeSuite")
        if youtube is None:
            await interaction.response.send_message("YouTube/Media-Berechtigungssystem ist nicht geladen.", ephemeral=True)
            return False
        try:
            allowed = await youtube._is_queue_mod(interaction.guild_id, interaction.user.id)
        except AttributeError:
            allowed = interaction.user.id in set(self.bot.settings.owner_ids)
        if allowed:
            return True
        await interaction.response.send_message(
            "Du bist nicht für die private Media-Queue freigeschaltet. Der Bot-Owner kann dich mit `/media youtube mod` freischalten.",
            ephemeral=True,
        )
        return False

    async def _require_owner(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id in set(self.bot.settings.owner_ids):
            return True
        await interaction.response.send_message("Nur der Bot-Owner darf diese Spotify-Verwaltungsfunktion nutzen.", ephemeral=True)
        return False

    async def _spotify_token(self) -> str:
        if self._token and time.monotonic() < self._token_expires_at - 30:
            return self._token
        client_id, client_secret = self._client_credentials()
        if not client_id or not client_secret:
            raise RuntimeError("Spotify API-Zugangsdaten sind nicht konfiguriert.")
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()
        async with self.session.post(
            SPOTIFY_TOKEN_URL,
            data={"grant_type": "client_credentials"},
            auth=aiohttp.BasicAuth(client_id, client_secret),
        ) as response:
            if response.status != 200:
                body = await response.text()
                raise RuntimeError(f"Spotify Token-Request fehlgeschlagen ({response.status}): {body[:160]}")
            data = await response.json()
        token = str(data.get("access_token") or "").strip()
        if not token:
            raise RuntimeError("Spotify hat kein Access-Token geliefert.")
        expires_in = max(60, int(data.get("expires_in") or 3600))
        self._token = token
        self._token_expires_at = time.monotonic() + expires_in
        return token

    async def _api_get(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        token = await self._spotify_token()
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()
        async with self.session.get(
            f"{SPOTIFY_API_BASE}{path}",
            params=params,
            headers={"Authorization": f"Bearer {token}"},
        ) as response:
            if response.status == 401:
                self._token = ""
                self._token_expires_at = 0.0
            if response.status != 200:
                body = await response.text()
                raise RuntimeError(f"Spotify API HTTP {response.status}: {body[:180]}")
            data = await response.json()
        if not isinstance(data, dict):
            raise RuntimeError("Spotify API lieferte eine unerwartete Antwort.")
        return data

    @staticmethod
    def _entity_from_url(value: str) -> tuple[str, str, str] | None:
        raw = value.strip()
        if raw.startswith("spotify:"):
            parts = raw.split(":")
            if len(parts) == 3 and parts[1] in {"track", "album", "playlist"} and parts[2]:
                entity_type, entity_id = parts[1], parts[2]
                return entity_type, entity_id, f"https://open.spotify.com/{entity_type}/{entity_id}"
            return None
        try:
            parsed = urlparse(raw)
        except ValueError:
            return None
        host = (parsed.hostname or "").lower().removeprefix("www.")
        if host != "open.spotify.com":
            return None
        parts = [part for part in parsed.path.split("/") if part]
        if parts and parts[0].startswith("intl-"):
            parts = parts[1:]
        if len(parts) < 2 or parts[0] not in {"track", "album", "playlist"}:
            return None
        entity_type = parts[0]
        entity_id = "".join(ch for ch in parts[1] if ch.isalnum())[:64]
        if not entity_id:
            return None
        return entity_type, entity_id, f"https://open.spotify.com/{entity_type}/{entity_id}"

    async def _resolve_short_link(self, value: str) -> str:
        raw = value.strip()
        try:
            parsed = urlparse(raw)
        except ValueError:
            return raw
        host = (parsed.hostname or "").lower().removeprefix("www.")
        if host != "spotify.link":
            return raw
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()
        async with self.session.get(raw, allow_redirects=True) as response:
            if response.status < 200 or response.status >= 400:
                raise ValueError(f"Spotify Kurzlink konnte nicht aufgelöst werden (HTTP {response.status}).")
            return str(response.url)

    @staticmethod
    def _images(item: dict[str, Any]) -> str:
        images = item.get("images") or []
        if isinstance(images, list):
            for image in images:
                if isinstance(image, dict) and str(image.get("url") or "").startswith("https://"):
                    return str(image["url"])[:1000]
        return ""

    @staticmethod
    def _artists(item: dict[str, Any]) -> str:
        artists = item.get("artists") or []
        names = [str(artist.get("name") or "").strip() for artist in artists if isinstance(artist, dict)]
        return ", ".join(name for name in names if name)[:180]

    def _track_from_api(self, item: dict[str, Any], requested_by: int) -> SpotifyTrack:
        external = item.get("external_urls") if isinstance(item.get("external_urls"), dict) else {}
        spotify_url = str(external.get("spotify") or "").strip()
        spotify_id = str(item.get("id") or "").strip()
        if not spotify_url and spotify_id:
            spotify_url = f"https://open.spotify.com/track/{spotify_id}"
        album_data = item.get("album") if isinstance(item.get("album"), dict) else {}
        thumbnail = self._images(album_data)
        return SpotifyTrack(
            spotify_url=spotify_url,
            title=str(item.get("name") or "Spotify Track").strip()[:220],
            artist=self._artists(item),
            album=str(album_data.get("name") or "").strip()[:180],
            thumbnail=thumbnail,
            requested_by=requested_by,
            spotify_id=spotify_id[:64],
            duration_ms=int(item["duration_ms"]) if item.get("duration_ms") is not None else None,
        )

    async def _track_oembed(self, url: str, entity_id: str, requested_by: int) -> SpotifyTrack:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()
        async with self.session.get(SPOTIFY_OEMBED_URL, params={"url": url}) as response:
            if response.status != 200:
                raise ValueError(f"Spotify-Link konnte nicht gelesen werden (oEmbed HTTP {response.status}).")
            data = await response.json()
        title = str(data.get("title") or "Spotify Track").strip()[:220]
        thumbnail = str(data.get("thumbnail_url") or "").strip()[:1000]
        return SpotifyTrack(
            spotify_url=url,
            title=title,
            artist="",
            album="",
            thumbnail=thumbnail if thumbnail.startswith("https://") else "",
            requested_by=requested_by,
            spotify_id=entity_id,
            duration_ms=None,
        )

    async def _resolve_track_url(self, url: str, entity_id: str, requested_by: int) -> SpotifyTrack:
        if self._credentials_configured():
            item = await self._api_get(f"/tracks/{entity_id}")
            return self._track_from_api(item, requested_by)
        return await self._track_oembed(url, entity_id, requested_by)

    async def _search_track(self, query: str, requested_by: int) -> SpotifyTrack:
        if not self._credentials_configured():
            raise ValueError(
                "Spotify-Suche per Text benötigt `SPOTIFY_CLIENT_ID` und `SPOTIFY_CLIENT_SECRET`. Ohne Zugangsdaten kannst du direkte Spotify-Tracklinks verwenden."
            )
        data = await self._api_get("/search", params={"q": query, "type": "track", "limit": 1, "market": "DE"})
        tracks = data.get("tracks") if isinstance(data.get("tracks"), dict) else {}
        items = tracks.get("items") if isinstance(tracks.get("items"), list) else []
        if not items or not isinstance(items[0], dict):
            raise ValueError("Kein Spotify-Treffer gefunden.")
        return self._track_from_api(items[0], requested_by)

    async def resolve_one(self, value: str, requested_by: int) -> SpotifyTrack:
        clean = " ".join(str(value or "").split()).strip()
        if not clean:
            raise ValueError("Spotify-Link oder Suchbegriff fehlt.")
        if clean.startswith("https://spotify.link/"):
            clean = await self._resolve_short_link(clean)
        entity = self._entity_from_url(clean)
        if entity is None:
            if "://" in clean or clean.startswith("spotify:"):
                raise ValueError("Unterstützt werden Spotify Track-, Album- und Playlist-Links.")
            return await self._search_track(clean, requested_by)
        entity_type, entity_id, canonical = entity
        if entity_type != "track":
            raise ValueError("Zum direkten Abspielen bitte einen Tracklink nutzen. Album/Playlist kannst du mit `/media spotify add` in die Queue übernehmen.")
        return await self._resolve_track_url(canonical, entity_id, requested_by)

    async def expand(self, value: str, requested_by: int, limit: int = MAX_QUEUE_PER_GUILD) -> list[SpotifyTrack]:
        clean = " ".join(str(value or "").split()).strip()
        if not clean:
            raise ValueError("Spotify-Link oder Suchbegriff fehlt.")
        if clean.startswith("https://spotify.link/"):
            clean = await self._resolve_short_link(clean)
        entity = self._entity_from_url(clean)
        if entity is None:
            return [await self._search_track(clean, requested_by)]
        entity_type, entity_id, canonical = entity
        if entity_type == "track":
            return [await self._resolve_track_url(canonical, entity_id, requested_by)]
        if not self._credentials_configured():
            raise ValueError(
                "Album- und Playlist-Import benötigen `SPOTIFY_CLIENT_ID` und `SPOTIFY_CLIENT_SECRET`. Einzelne Tracklinks funktionieren auch ohne Spotify-App."
            )

        tracks: list[SpotifyTrack] = []
        if entity_type == "album":
            album = await self._api_get(f"/albums/{entity_id}", params={"market": "DE"})
            album_name = str(album.get("name") or "").strip()[:180]
            album_image = self._images(album)
            track_data = album.get("tracks") if isinstance(album.get("tracks"), dict) else {}
            items = track_data.get("items") if isinstance(track_data.get("items"), list) else []
            for item in items[:limit]:
                if not isinstance(item, dict):
                    continue
                track = self._track_from_api(item, requested_by)
                track.album = album_name
                if not track.thumbnail:
                    track.thumbnail = album_image
                tracks.append(track)
        elif entity_type == "playlist":
            data = await self._api_get(
                f"/playlists/{entity_id}/tracks",
                params={"market": "DE", "limit": min(50, limit), "offset": 0},
            )
            items = data.get("items") if isinstance(data.get("items"), list) else []
            for wrapper in items:
                if len(tracks) >= limit:
                    break
                if not isinstance(wrapper, dict):
                    continue
                item = wrapper.get("track") if isinstance(wrapper.get("track"), dict) else None
                if not item or item.get("is_local"):
                    continue
                tracks.append(self._track_from_api(item, requested_by))

        if not tracks:
            raise ValueError("Keine abspielbaren Spotify-Tracks gefunden.")
        return tracks

    async def _persist_runtime(self, guild_id: int) -> None:
        current = self.current.get(guild_id)
        queue = list(self.queues.get(guild_id, ()))
        payload = {
            "active": guild_id in self.session_active,
            "credentials_configured": self._credentials_configured(),
            "current": asdict(current) if current else None,
            "queue": [asdict(item) for item in queue[:MAX_QUEUE_PER_GUILD]],
        }
        import json

        await self.bot.database.execute(
            """
            INSERT INTO spotify_runtime_state(guild_id,state_json,updated_at)
            VALUES(?,?,CURRENT_TIMESTAMP)
            ON CONFLICT(guild_id) DO UPDATE SET
                state_json=excluded.state_json,
                updated_at=CURRENT_TIMESTAMP
            """,
            (guild_id, json.dumps(payload, ensure_ascii=False, separators=(",", ":"))),
        )

    async def _resolve_audio(self, track: SpotifyTrack):
        youtube = self._youtube()
        return await youtube._resolve(track.audio_query, track.requested_by)

    def _deactivate_youtube_session(self, guild_id: int) -> None:
        youtube = self.bot.get_cog("YouTubeSuite")
        if youtube is None:
            return
        getattr(youtube, "session_active", set()).discard(guild_id)
        getattr(youtube, "loop_enabled", set()).discard(guild_id)
        getattr(youtube, "current", {}).pop(guild_id, None)

    async def _start_track_on_voice(
        self,
        guild: discord.Guild,
        voice: discord.VoiceClient,
        track: SpotifyTrack,
        *,
        volume: int = DEFAULT_VOLUME,
    ) -> None:
        youtube = self._youtube()
        voice_cog = self._voice()
        resolved = await self._resolve_audio(track)
        self._deactivate_youtube_session(guild.id)
        await voice_cog._start_on_voice(
            voice,
            youtube._audio_source(resolved),
            guild_id=guild.id,
            title=track.display_title,
            kind="Spotify",
            started_by=track.requested_by,
            source_name=track.spotify_url,
            volume=max(10, min(120, int(volume))),
        )
        self.current[guild.id] = track
        self.session_active.add(guild.id)
        await self._persist_runtime(guild.id)

    async def _start_interaction_track(
        self,
        interaction: discord.Interaction,
        track: SpotifyTrack,
        *,
        volume: int = DEFAULT_VOLUME,
    ) -> None:
        if interaction.guild is None:
            raise ValueError("Dieser Command funktioniert nur auf einem Server.")
        voice_cog = self._voice()
        voice = await voice_cog._get_voice(interaction)
        if voice is None:
            raise ValueError("Du musst in einem Voice-Channel sein.")
        await self._start_track_on_voice(interaction.guild, voice, track, volume=volume)

    async def _start_queued(self, guild_id: int, track: SpotifyTrack) -> None:
        if guild_id in self.starting:
            return
        self.starting.add(guild_id)
        try:
            guild = self.bot.get_guild(guild_id)
            if guild is None or guild.voice_client is None or not guild.voice_client.is_connected():
                self.session_active.discard(guild_id)
                self.current.pop(guild_id, None)
                await self._persist_runtime(guild_id)
                return
            await self._start_track_on_voice(guild, guild.voice_client, track, volume=DEFAULT_VOLUME)
        except Exception as exc:
            logger.warning("Could not start queued Spotify item in guild %s: %s", guild_id, exc)
            self.current.pop(guild_id, None)
            await self._persist_runtime(guild_id)
        finally:
            self.starting.discard(guild_id)

    @staticmethod
    def _duration_text(seconds: int | None) -> str:
        if not seconds:
            return "Unbekannt"
        hours, rem = divmod(max(0, int(seconds)), 3600)
        minutes, secs = divmod(rem, 60)
        return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"

    def _track_embed(self, title: str, track: SpotifyTrack, *, extra: str = "") -> discord.Embed:
        description = f"**{track.display_title}**"
        if track.album:
            description += f"\nAlbum: **{track.album}**"
        description += f"\nDauer: **{self._duration_text(track.duration_seconds)}**"
        if extra:
            description += f"\n{extra}"
        embed = discord.Embed(title=title, description=description, color=SPOTIFY_GREEN, url=track.spotify_url or None)
        embed.set_author(name="HomePi Spotify Bridge")
        if track.thumbnail:
            embed.set_thumbnail(url=track.thumbnail)
        embed.set_footer(text="Spotify liefert Metadaten · Discord-Audio wird über die bestehende YouTube/yt-dlp-Pipeline aufgelöst")
        return embed

    @app_commands.command(name="play", description="Spielt einen Spotify-Tracklink bzw. mit API-Zugang auch einen Suchbegriff.")
    @app_commands.describe(quelle="Spotify Track-URL/URI oder Suchbegriff", lautstaerke="10 bis 120 Prozent")
    async def play(
        self,
        interaction: discord.Interaction,
        quelle: str,
        lautstaerke: app_commands.Range[int, 10, 120] = DEFAULT_VOLUME,
    ) -> None:
        if interaction.guild_id is None or not await self._require_queue_access(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        try:
            track = await self.resolve_one(quelle, interaction.user.id)
            await self._start_interaction_track(interaction, track, volume=int(lautstaerke))
        except (ValueError, RuntimeError, asyncio.TimeoutError) as exc:
            await interaction.followup.send(f"Spotify konnte nicht gestartet werden: {exc}", ephemeral=True)
            return
        except Exception as exc:
            logger.exception("Spotify play failed")
            await interaction.followup.send(f"Spotify-Wiedergabe fehlgeschlagen: {type(exc).__name__}", ephemeral=True)
            return
        await interaction.followup.send(
            embed=self._track_embed("▶️ Spotify", track, extra=f"Lautstärke: **{int(lautstaerke)}%**"),
            ephemeral=True,
        )

    @app_commands.command(name="add", description="Fügt Spotify Track/Album/Playlist zur privaten Media-Queue hinzu.")
    @app_commands.describe(quelle="Spotify Track-, Album- oder Playlist-Link; Suche mit API-Zugang")
    async def add(self, interaction: discord.Interaction, quelle: str) -> None:
        if interaction.guild_id is None or not await self._require_queue_access(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        queue = self.queues[interaction.guild_id]
        remaining = MAX_QUEUE_PER_GUILD - len(queue)
        if remaining <= 0:
            await interaction.followup.send(f"Die Spotify-Queue ist auf {MAX_QUEUE_PER_GUILD} Titel begrenzt.", ephemeral=True)
            return
        try:
            tracks = await self.expand(quelle, interaction.user.id, limit=remaining)
        except (ValueError, RuntimeError, asyncio.TimeoutError) as exc:
            await interaction.followup.send(f"Spotify konnte nicht zur Queue hinzugefügt werden: {exc}", ephemeral=True)
            return
        for track in tracks[:remaining]:
            queue.append(track)
        await self._persist_runtime(interaction.guild_id)
        first = tracks[0]
        extra = f"**{min(len(tracks), remaining)}** Titel hinzugefügt · Queue jetzt **{len(queue)}**"
        await interaction.followup.send(embed=self._track_embed("➕ Spotify Queue", first, extra=extra), ephemeral=True)

    @app_commands.command(name="queue", description="Zeigt die Spotify-Queue.")
    async def queue(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None or not await self._require_queue_access(interaction):
            return
        current = self.current.get(interaction.guild_id)
        queued = list(self.queues.get(interaction.guild_id, ()))
        lines = [f"**Jetzt:** {current.display_title}" if current else "**Jetzt:** —"]
        if queued:
            lines.append("")
            for index, track in enumerate(queued[:20], start=1):
                lines.append(f"`{index:02d}` **{track.display_title}** · {self._duration_text(track.duration_seconds)}")
        else:
            lines.append("\nQueue leer.")
        embed = discord.Embed(title="🟢 Spotify Queue", description="\n".join(lines), color=SPOTIFY_GREEN)
        embed.set_footer(text="Max. 25 Titel · gleiche Queue-Mod-Berechtigungen wie YouTube")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="skip", description="Owner-only: überspringt den aktuellen Spotify-Titel.")
    async def skip(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None or not await self._require_owner(interaction):
            return
        voice = interaction.guild.voice_client if interaction.guild else None
        if voice is None or not (voice.is_playing() or voice.is_paused()):
            await interaction.response.send_message("Aktuell läuft kein Spotify-Titel.", ephemeral=True)
            return
        self.current.pop(interaction.guild_id, None)
        voice.stop()
        await self._persist_runtime(interaction.guild_id)
        await interaction.response.send_message("⏭️ Spotify-Titel übersprungen.", ephemeral=True)

    @app_commands.command(name="stop", description="Owner-only: beendet die Spotify-Session; Queue bleibt erhalten.")
    async def stop(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None or not await self._require_owner(interaction):
            return
        self.session_active.discard(interaction.guild_id)
        self.current.pop(interaction.guild_id, None)
        voice = interaction.guild.voice_client if interaction.guild else None
        if voice and (voice.is_playing() or voice.is_paused()):
            state = getattr(self._voice(), "states", {}).get(interaction.guild_id)
            if state and str(getattr(state, "kind", "")).lower() == "spotify":
                voice.stop()
        await self._persist_runtime(interaction.guild_id)
        await interaction.response.send_message("⏹️ Spotify-Session beendet. Die Queue bleibt erhalten.", ephemeral=True)

    @app_commands.command(name="clear", description="Owner-only: leert die Spotify-Queue.")
    async def clear(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None or not await self._require_owner(interaction):
            return
        count = len(self.queues.get(interaction.guild_id, ()))
        self.queues[interaction.guild_id].clear()
        await self._persist_runtime(interaction.guild_id)
        await interaction.response.send_message(f"🧹 **{count}** Spotify-Queue-Einträge entfernt.", ephemeral=True)

    @app_commands.command(name="status", description="Zeigt den Spotify-Integrationsstatus.")
    async def status(self, interaction: discord.Interaction) -> None:
        credentials = self._credentials_configured()
        embed = discord.Embed(
            title="🟢 Spotify Integration",
            description=(
                "**Spotify Web API:** " + ("✅ konfiguriert" if credentials else "⚪ optional, nicht konfiguriert") +
                "\n**Direkte Tracklinks:** ✅" +
                ("\n**Textsuche / Album / Playlist:** ✅" if credentials else "\n**Textsuche / Album / Playlist:** benötigt Spotify-App-Zugangsdaten") +
                "\n\nSpotify-Audio wird nicht direkt extrahiert. Der Link liefert Metadaten; der passende Titel wird über die bestehende Audio-Resolver-Pipeline abgespielt."
            ),
            color=SPOTIFY_GREEN,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def dashboard_play(self, guild_id: int, channel_id: int, source: str, volume: int = DEFAULT_VOLUME) -> str:
        guild = self.bot.get_guild(int(guild_id))
        if guild is None:
            raise ValueError("Guild not found")
        channel = guild.get_channel(int(channel_id))
        if not isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
            raise ValueError("Voice channel not found")
        track = await self.resolve_one(source, 0)
        voice_cog = self._voice()
        voice = await voice_cog._connect_channel(guild, channel)
        await self._start_track_on_voice(guild, voice, track, volume=volume)
        return f"Spotify started: {track.display_title}"

    async def dashboard_add(self, guild_id: int, source: str, requested_by: int = 0) -> str:
        queue = self.queues[int(guild_id)]
        remaining = MAX_QUEUE_PER_GUILD - len(queue)
        if remaining <= 0:
            raise ValueError("Spotify queue is full")
        tracks = await self.expand(source, requested_by, limit=remaining)
        for track in tracks[:remaining]:
            queue.append(track)
        await self._persist_runtime(int(guild_id))
        return f"Spotify queued: {min(len(tracks), remaining)} item(s); queue={len(queue)}"

    @tasks.loop(seconds=2)
    async def queue_guard(self) -> None:
        voice_cog = self.bot.get_cog("VoiceSuite")
        for guild_id in list(self.session_active):
            if guild_id in self.starting:
                continue
            guild = self.bot.get_guild(guild_id)
            voice = guild.voice_client if guild else None
            state = getattr(voice_cog, "states", {}).get(guild_id) if voice_cog else None
            if voice is None or not voice.is_connected():
                self.session_active.discard(guild_id)
                self.current.pop(guild_id, None)
                await self._persist_runtime(guild_id)
                continue
            if state is None or str(getattr(state, "kind", "")).lower() != "spotify":
                self.session_active.discard(guild_id)
                self.current.pop(guild_id, None)
                await self._persist_runtime(guild_id)
                continue
            if voice.is_playing() or voice.is_paused():
                continue
            self.current.pop(guild_id, None)
            queue = self.queues.get(guild_id)
            if queue:
                await self._start_queued(guild_id, queue.popleft())
            else:
                self.session_active.discard(guild_id)
                await self._persist_runtime(guild_id)

    @queue_guard.before_loop
    async def before_queue_guard(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SpotifySuite(bot))
