from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from collections import deque
from types import MethodType
from typing import Any

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from cogs.community import spotify_suite as spotify_module
from cogs.community.spotify_suite import DEFAULT_VOLUME, SpotifyTrack

logger = logging.getLogger(__name__)

MAX_QUEUE_PER_GUILD = 200
PLAYLIST_PAGE_SIZE = 50
MAX_AUTOSKIP_FAILURES = 8
SPOTIFY_GREEN = 0x1DB954


class SpotifyPlaylistPlus(commands.Cog):
    """Compatibility/update layer for Spotify's 2026 playlist API.

    Spotify's February 2026 API changes moved playlist item reads to
    /playlists/{id}/items and require user OAuth for playlists owned by or shared
    with the authorized account. This extension keeps normal client-credential
    track/album search while adding user OAuth, pagination and full playlist play.
    """

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.suite = bot.get_cog("SpotifySuite")
        if self.suite is None:
            raise RuntimeError("SpotifySuite must be loaded before SpotifyPlaylistPlus")

        self._client_token = ""
        self._client_token_expires_at = 0.0
        self._user_token = ""
        self._user_token_expires_at = 0.0
        self._session_volume: dict[int, int] = {}
        self._original_commands: dict[str, app_commands.Command | app_commands.Group] = {}
        self._original_methods: dict[str, Any] = {}
        self._old_max_queue = spotify_module.MAX_QUEUE_PER_GUILD

    async def cog_load(self) -> None:
        spotify_module.MAX_QUEUE_PER_GUILD = MAX_QUEUE_PER_GUILD
        self._patch_suite()
        await self._restore_queues()
        self._install_commands()
        logger.info(
            "Spotify playlist plus active: max_queue=%s user_oauth=%s",
            MAX_QUEUE_PER_GUILD,
            self._user_auth_configured(),
        )

    async def cog_unload(self) -> None:
        self._restore_commands()
        for name, method in self._original_methods.items():
            setattr(self.suite, name, method)
        spotify_module.MAX_QUEUE_PER_GUILD = self._old_max_queue

    def _settings_value(self, name: str, default: str = "") -> str:
        return str(getattr(self.bot.settings, name, default) or default).strip()

    def _client_credentials(self) -> tuple[str, str]:
        return (
            self._settings_value("spotify_client_id"),
            self._settings_value("spotify_client_secret"),
        )

    def _credentials_configured(self) -> bool:
        client_id, client_secret = self._client_credentials()
        return bool(client_id and client_secret)

    def _user_auth_configured(self) -> bool:
        return bool(self._credentials_configured() and self._settings_value("spotify_refresh_token"))

    def _market(self) -> str:
        value = self._settings_value("spotify_market", "DE").upper()
        return value if len(value) == 2 and value.isalpha() else "DE"

    async def _token_request(self, data: dict[str, str]) -> dict[str, Any]:
        client_id, client_secret = self._client_credentials()
        if not client_id or not client_secret:
            raise RuntimeError("Spotify Client ID/Secret sind nicht konfiguriert.")
        session = self.suite.session
        if session is None or session.closed:
            session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15, connect=5, sock_connect=5, sock_read=10)
            )
            self.suite.session = session
        async with session.post(
            spotify_module.SPOTIFY_TOKEN_URL,
            data=data,
            auth=aiohttp.BasicAuth(client_id, client_secret),
        ) as response:
            body = await response.text()
            if response.status != 200:
                raise RuntimeError(f"Spotify Token HTTP {response.status}: {body[:220]}")
            try:
                payload = json.loads(body)
            except json.JSONDecodeError as exc:
                raise RuntimeError("Spotify Token-Antwort ist kein gültiges JSON.") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("Spotify Token-Antwort ist ungültig.")
        return payload

    async def _get_client_token(self, *, force: bool = False) -> str:
        if not force and self._client_token and time.monotonic() < self._client_token_expires_at - 30:
            return self._client_token
        payload = await self._token_request({"grant_type": "client_credentials"})
        token = str(payload.get("access_token") or "").strip()
        if not token:
            raise RuntimeError("Spotify hat kein Client-Credentials Access Token geliefert.")
        self._client_token = token
        self._client_token_expires_at = time.monotonic() + max(60, int(payload.get("expires_in") or 3600))
        return token

    async def _get_user_token(self, *, force: bool = False) -> str:
        if not self._user_auth_configured():
            raise RuntimeError(
                "Playlist-Zugriff benötigt User-OAuth. Führe auf dem Pi `python scripts/spotify_setup.py auth-url` aus."
            )
        if not force and self._user_token and time.monotonic() < self._user_token_expires_at - 30:
            return self._user_token
        refresh_token = self._settings_value("spotify_refresh_token")
        payload = await self._token_request(
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            }
        )
        token = str(payload.get("access_token") or "").strip()
        if not token:
            raise RuntimeError("Spotify hat beim Refresh kein User Access Token geliefert.")
        self._user_token = token
        self._user_token_expires_at = time.monotonic() + max(60, int(payload.get("expires_in") or 3600))
        return token

    async def _api_get_impl(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        require_user: bool = False,
        retry: bool = True,
    ) -> dict[str, Any]:
        token = await (self._get_user_token() if require_user else self._get_client_token())
        session = self.suite.session
        if session is None or session.closed:
            session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15, connect=5, sock_connect=5, sock_read=10)
            )
            self.suite.session = session

        url = path if path.startswith("https://") else f"{spotify_module.SPOTIFY_API_BASE}{path}"
        async with session.get(
            url,
            params=params,
            headers={"Authorization": f"Bearer {token}"},
        ) as response:
            if response.status == 429 and retry:
                try:
                    wait = min(10.0, max(0.5, float(response.headers.get("Retry-After", "1"))))
                except ValueError:
                    wait = 1.0
                await asyncio.sleep(wait)
                return await self._api_get_impl(
                    path,
                    params=params,
                    require_user=require_user,
                    retry=False,
                )
            if response.status == 401 and retry:
                if require_user:
                    self._user_token = ""
                    self._user_token_expires_at = 0.0
                    await self._get_user_token(force=True)
                else:
                    self._client_token = ""
                    self._client_token_expires_at = 0.0
                    await self._get_client_token(force=True)
                return await self._api_get_impl(
                    path,
                    params=params,
                    require_user=require_user,
                    retry=False,
                )
            body = await response.text()
            if response.status != 200:
                hint = ""
                if response.status == 403 and require_user:
                    hint = (
                        " Die Spotify-API erlaubt seit 2026 Playlist-Items nur für Playlists, "
                        "die dem autorisierten Account gehören oder bei denen er Collaborator ist."
                    )
                raise RuntimeError(f"Spotify API HTTP {response.status}: {body[:220]}{hint}")
            try:
                payload = json.loads(body)
            except json.JSONDecodeError as exc:
                raise RuntimeError("Spotify API lieferte ungültiges JSON.") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("Spotify API lieferte eine unerwartete Antwort.")
        return payload

    async def _expand_impl(
        self,
        value: str,
        requested_by: int,
        limit: int = MAX_QUEUE_PER_GUILD,
    ) -> list[SpotifyTrack]:
        clean = " ".join(str(value or "").split()).strip()
        if not clean:
            raise ValueError("Spotify-Link oder Suchbegriff fehlt.")
        if clean.startswith("https://spotify.link/"):
            clean = await self.suite._resolve_short_link(clean)

        entity = self.suite._entity_from_url(clean)
        if entity is None:
            if "://" in clean or clean.startswith("spotify:"):
                raise ValueError("Unterstützt werden Spotify Track-, Album- und Playlist-Links.")
            return [await self.suite._search_track(clean, requested_by)]

        entity_type, entity_id, canonical = entity
        if entity_type == "track":
            return [await self.suite._resolve_track_url(canonical, entity_id, requested_by)]
        if not self._credentials_configured():
            raise ValueError(
                "Album/Playlist benötigen Spotify API-Zugang. Richte zuerst Client ID + Secret mit "
                "`python scripts/spotify_setup.py credentials` ein."
            )

        limit = max(1, min(MAX_QUEUE_PER_GUILD, int(limit)))
        tracks: list[SpotifyTrack] = []

        if entity_type == "album":
            album = await self._api_get_impl(
                f"/albums/{entity_id}",
                params={"market": self._market()},
            )
            album_name = str(album.get("name") or "").strip()[:180]
            album_image = self.suite._images(album)
            page = album.get("tracks") if isinstance(album.get("tracks"), dict) else {}
            while page and len(tracks) < limit:
                items = page.get("items") if isinstance(page.get("items"), list) else []
                for item in items:
                    if len(tracks) >= limit:
                        break
                    if not isinstance(item, dict) or item.get("type") not in {None, "track"}:
                        continue
                    track = self.suite._track_from_api(item, requested_by)
                    track.album = album_name
                    if not track.thumbnail:
                        track.thumbnail = album_image
                    tracks.append(track)
                next_url = str(page.get("next") or "").strip()
                if not next_url or len(tracks) >= limit:
                    break
                page = await self._api_get_impl(next_url)

        elif entity_type == "playlist":
            if not self._user_auth_configured():
                raise ValueError(
                    "Spotify-Playlists brauchen seit 2026 User-OAuth. Richte nach Client ID/Secret noch "
                    "`python scripts/spotify_setup.py auth-url` ein und tausche die Callback-URL mit "
                    "`python scripts/spotify_setup.py exchange '<URL>'` gegen einen Refresh Token."
                )
            offset = 0
            while len(tracks) < limit:
                page = await self._api_get_impl(
                    f"/playlists/{entity_id}/items",
                    params={
                        "market": self._market(),
                        "limit": min(PLAYLIST_PAGE_SIZE, limit - len(tracks)),
                        "offset": offset,
                        "additional_types": "track",
                    },
                    require_user=True,
                )
                items = page.get("items") if isinstance(page.get("items"), list) else []
                if not items:
                    break
                for wrapper in items:
                    if len(tracks) >= limit:
                        break
                    if not isinstance(wrapper, dict):
                        continue
                    item = wrapper.get("track")
                    if not isinstance(item, dict):
                        item = wrapper.get("item") if isinstance(wrapper.get("item"), dict) else None
                    if not item:
                        continue
                    if item.get("type") not in {None, "track"} or item.get("is_local"):
                        continue
                    tracks.append(self.suite._track_from_api(item, requested_by))
                offset += len(items)
                if not page.get("next") or offset >= int(page.get("total") or offset):
                    break

        if not tracks:
            raise ValueError("Keine abspielbaren Spotify-Tracks gefunden.")
        return tracks

    async def _persist_runtime_impl(self, guild_id: int) -> None:
        current = self.suite.current.get(guild_id)
        queue = list(self.suite.queues.get(guild_id, ()))
        voice_cog = self.bot.get_cog("VoiceSuite")
        voice_state = getattr(voice_cog, "states", {}).get(guild_id) if voice_cog else None
        volume = self._session_volume.get(guild_id)
        if voice_state is not None:
            try:
                volume = int(getattr(voice_state, "volume", volume or DEFAULT_VOLUME))
            except (TypeError, ValueError):
                pass
        payload = {
            "active": guild_id in self.suite.session_active,
            "credentials_configured": self._credentials_configured(),
            "user_auth_configured": self._user_auth_configured(),
            "playlist_access_configured": self._user_auth_configured(),
            "max_queue": MAX_QUEUE_PER_GUILD,
            "volume": volume,
            "current": spotify_module.asdict(current) if current else None,
            "queue": [spotify_module.asdict(item) for item in queue[:MAX_QUEUE_PER_GUILD]],
        }
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

    async def _start_queued_impl(self, guild_id: int, first_track: SpotifyTrack) -> None:
        if guild_id in self.suite.starting:
            return
        self.suite.starting.add(guild_id)
        try:
            guild = self.bot.get_guild(guild_id)
            if guild is None or guild.voice_client is None or not guild.voice_client.is_connected():
                self.suite.session_active.discard(guild_id)
                self.suite.current.pop(guild_id, None)
                await self._persist_runtime_impl(guild_id)
                return

            voice_cog = self.bot.get_cog("VoiceSuite")
            state = getattr(voice_cog, "states", {}).get(guild_id) if voice_cog else None
            volume = self._session_volume.get(guild_id, DEFAULT_VOLUME)
            if state is not None:
                try:
                    volume = int(getattr(state, "volume", volume))
                except (TypeError, ValueError):
                    pass
            self._session_volume[guild_id] = max(10, min(120, volume))

            candidate: SpotifyTrack | None = first_track
            failures = 0
            while candidate is not None and failures < MAX_AUTOSKIP_FAILURES:
                try:
                    await self.suite._start_track_on_voice(
                        guild,
                        guild.voice_client,
                        candidate,
                        volume=self._session_volume[guild_id],
                    )
                    return
                except Exception as exc:
                    failures += 1
                    logger.warning(
                        "Spotify queued track failed guild=%s track=%s attempt=%s: %s",
                        guild_id,
                        candidate.display_title,
                        failures,
                        exc,
                    )
                    self.suite.current.pop(guild_id, None)
                    queue = self.suite.queues.get(guild_id)
                    candidate = queue.popleft() if queue else None

            if candidate is None:
                self.suite.session_active.discard(guild_id)
            await self._persist_runtime_impl(guild_id)
        finally:
            self.suite.starting.discard(guild_id)

    async def _dashboard_play_impl(
        self,
        guild_id: int,
        channel_id: int,
        source: str,
        volume: int = DEFAULT_VOLUME,
    ) -> str:
        guild_id = int(guild_id)
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise ValueError("Guild not found")
        channel = guild.get_channel(int(channel_id))
        if not isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
            raise ValueError("Voice channel not found")

        tracks = await self._expand_impl(source, 0, limit=MAX_QUEUE_PER_GUILD)
        first, rest = tracks[0], tracks[1:]
        queue = self.suite.queues[guild_id]
        existing = list(queue)
        queue.clear()
        queue.extend(rest)
        queue.extend(existing)
        self._session_volume[guild_id] = max(10, min(120, int(volume)))

        voice_cog = self.suite._voice()
        voice = await voice_cog._connect_channel(guild, channel)
        await self.suite._start_track_on_voice(guild, voice, first, volume=self._session_volume[guild_id])
        await self._persist_runtime_impl(guild_id)
        return f"Spotify started: {first.display_title}; queued={len(rest)}"

    async def _restore_queues(self) -> None:
        try:
            rows = await self.bot.database.fetchall("SELECT guild_id,state_json FROM spotify_runtime_state")
        except Exception:
            logger.debug("Spotify persisted queue restore unavailable", exc_info=True)
            return

        for row in rows:
            try:
                guild_id = int(row["guild_id"])
                payload = json.loads(str(row["state_json"] or "{}"))
                if not isinstance(payload, dict):
                    continue
                queue_data = payload.get("queue") if isinstance(payload.get("queue"), list) else []
                restored: deque[SpotifyTrack] = deque()
                for raw in queue_data[:MAX_QUEUE_PER_GUILD]:
                    if not isinstance(raw, dict):
                        continue
                    restored.append(
                        SpotifyTrack(
                            spotify_url=str(raw.get("spotify_url") or ""),
                            title=str(raw.get("title") or "Spotify Track")[:220],
                            artist=str(raw.get("artist") or "")[:180],
                            album=str(raw.get("album") or "")[:180],
                            thumbnail=str(raw.get("thumbnail") or "")[:1000],
                            requested_by=int(raw.get("requested_by") or 0),
                            spotify_id=str(raw.get("spotify_id") or "")[:64],
                            duration_ms=(
                                int(raw["duration_ms"])
                                if raw.get("duration_ms") is not None
                                else None
                            ),
                        )
                    )
                self.suite.queues[guild_id] = restored
                self.suite.current.pop(guild_id, None)
                self.suite.session_active.discard(guild_id)
                saved_volume = payload.get("volume")
                if saved_volume is not None:
                    self._session_volume[guild_id] = max(10, min(120, int(saved_volume)))
                await self._persist_runtime_impl(guild_id)
            except (TypeError, ValueError, json.JSONDecodeError):
                logger.warning("Skipped invalid persisted Spotify queue row", exc_info=True)

    def _patch_suite(self) -> None:
        for name in (
            "_client_credentials",
            "_credentials_configured",
            "_api_get",
            "expand",
            "_persist_runtime",
            "_start_queued",
            "dashboard_play",
        ):
            self._original_methods[name] = getattr(self.suite, name)

        def client_credentials(_suite) -> tuple[str, str]:
            return self._client_credentials()

        def credentials_configured(_suite) -> bool:
            return self._credentials_configured()

        async def api_get(
            _suite,
            path: str,
            *,
            params: dict[str, Any] | None = None,
            require_user: bool = False,
        ) -> dict[str, Any]:
            return await self._api_get_impl(path, params=params, require_user=require_user)

        async def expand(
            _suite,
            value: str,
            requested_by: int,
            limit: int = MAX_QUEUE_PER_GUILD,
        ) -> list[SpotifyTrack]:
            return await self._expand_impl(value, requested_by, limit)

        async def persist_runtime(_suite, guild_id: int) -> None:
            await self._persist_runtime_impl(guild_id)

        async def start_queued(_suite, guild_id: int, track: SpotifyTrack) -> None:
            await self._start_queued_impl(guild_id, track)

        async def dashboard_play(
            _suite,
            guild_id: int,
            channel_id: int,
            source: str,
            volume: int = DEFAULT_VOLUME,
        ) -> str:
            return await self._dashboard_play_impl(guild_id, channel_id, source, volume)

        self.suite._client_credentials = MethodType(client_credentials, self.suite)
        self.suite._credentials_configured = MethodType(credentials_configured, self.suite)
        self.suite._api_get = MethodType(api_get, self.suite)
        self.suite.expand = MethodType(expand, self.suite)
        self.suite._persist_runtime = MethodType(persist_runtime, self.suite)
        self.suite._start_queued = MethodType(start_queued, self.suite)
        self.suite.dashboard_play = MethodType(dashboard_play, self.suite)

    def _spotify_group(self) -> app_commands.Group | None:
        media = self.bot.tree.get_command("media")
        if isinstance(media, app_commands.Group):
            spotify = media.get_command("spotify")
            if isinstance(spotify, app_commands.Group):
                return spotify
        root = self.bot.tree.get_command("spotify")
        return root if isinstance(root, app_commands.Group) else None

    def _replace_command(self, group: app_commands.Group, name: str, command: app_commands.Command) -> None:
        existing = group.get_command(name)
        if existing is not None:
            removed = group.remove_command(name)
            if removed is not None:
                removed.parent = None
                self._original_commands[name] = removed
        group.add_command(command)

    def _install_commands(self) -> None:
        group = self._spotify_group()
        if group is None:
            raise RuntimeError("Spotify application-command group not found")

        self._replace_command(
            group,
            "play",
            app_commands.Command(
                name="play",
                description="Spielt Track, Album oder Playlist; Rest wird automatisch eingereiht.",
                callback=self._play_command,
            ),
        )
        self._replace_command(
            group,
            "status",
            app_commands.Command(
                name="status",
                description="Zeigt Spotify API-, OAuth- und Playlist-Status.",
                callback=self._status_command,
            ),
        )

        extras = (
            app_commands.Command(
                name="shuffle",
                description="Mischt die aktuelle Spotify-Queue.",
                callback=self._shuffle_command,
            ),
            app_commands.Command(
                name="remove",
                description="Entfernt einen Titel an einer Queue-Position.",
                callback=self._remove_command,
            ),
            app_commands.Command(
                name="move",
                description="Verschiebt einen Titel innerhalb der Spotify-Queue.",
                callback=self._move_command,
            ),
            app_commands.Command(
                name="setup",
                description="Zeigt die lokalen Spotify API/OAuth Setup-Schritte.",
                callback=self._setup_command,
            ),
        )
        for command in extras:
            old = group.get_command(command.name)
            if old is not None:
                removed = group.remove_command(command.name)
                if removed is not None:
                    removed.parent = None
                    self._original_commands[command.name] = removed
            group.add_command(command)

    def _restore_commands(self) -> None:
        group = self._spotify_group()
        if group is None:
            return
        for name in ("play", "status", "shuffle", "remove", "move", "setup"):
            current = group.get_command(name)
            callback = getattr(current, "callback", None) if current is not None else None
            owner = getattr(callback, "__self__", None)
            if current is not None and owner is self:
                removed = group.remove_command(name)
                if removed is not None:
                    removed.parent = None
            original = self._original_commands.get(name)
            if original is not None and group.get_command(name) is None:
                group.add_command(original)

    @app_commands.describe(
        quelle="Spotify Track-, Album-, Playlist-Link oder Suchbegriff",
        lautstaerke="10 bis 120 Prozent",
        mischen="Bei Album/Playlist die importierten Titel vor dem Start mischen",
    )
    async def _play_command(
        self,
        interaction: discord.Interaction,
        quelle: str,
        lautstaerke: app_commands.Range[int, 10, 120] = DEFAULT_VOLUME,
        mischen: bool = False,
    ) -> None:
        if interaction.guild_id is None or interaction.guild is None:
            return
        if not await self.suite._require_queue_access(interaction):
            return
        await interaction.response.defer(ephemeral=True)

        try:
            tracks = await self._expand_impl(quelle, interaction.user.id, MAX_QUEUE_PER_GUILD)
            if mischen and len(tracks) > 1:
                random.shuffle(tracks)
            first, rest = tracks[0], tracks[1:]
            queue = self.suite.queues[interaction.guild_id]
            existing = list(queue)
            queue.clear()
            queue.extend(rest)
            queue.extend(existing)
            self._session_volume[interaction.guild_id] = int(lautstaerke)
            await self.suite._start_interaction_track(
                interaction,
                first,
                volume=int(lautstaerke),
            )
            await self._persist_runtime_impl(interaction.guild_id)
        except (ValueError, RuntimeError, asyncio.TimeoutError) as exc:
            await interaction.followup.send(f"Spotify konnte nicht gestartet werden: {exc}", ephemeral=True)
            return
        except Exception as exc:
            logger.exception("Improved Spotify play failed")
            await interaction.followup.send(
                f"Spotify-Wiedergabe fehlgeschlagen: {type(exc).__name__}",
                ephemeral=True,
            )
            return

        source_type = "Playlist/Album" if len(tracks) > 1 else "Track"
        extra = (
            f"{source_type} gestartet · **{len(rest)}** Titel als Nächstes eingereiht · "
            f"Queue gesamt **{len(self.suite.queues[interaction.guild_id])}** · Lautstärke **{int(lautstaerke)}%**"
        )
        await interaction.followup.send(
            embed=self.suite._track_embed("▶️ Spotify", first, extra=extra),
            ephemeral=True,
        )

    async def _status_command(self, interaction: discord.Interaction) -> None:
        client = self._credentials_configured()
        user = self._user_auth_configured()
        description = (
            f"**Client ID + Secret:** {'✅' if client else '❌'}\n"
            f"**User OAuth / Refresh Token:** {'✅' if user else '❌'}\n"
            "**Direkte Tracklinks:** ✅\n"
            f"**Textsuche + Alben:** {'✅' if client else '❌'}\n"
            f"**Eigene/kollaborative Playlists:** {'✅' if user else '❌'}\n"
            f"**Queue-Limit:** {MAX_QUEUE_PER_GUILD} Titel\n"
            f"**Market:** `{self._market()}`\n\n"
            "Spotify liefert nur Metadaten; das Discord-Audio wird weiterhin über die bestehende Audio-Resolver-Pipeline abgespielt."
        )
        if client and not user:
            description += (
                "\n\nFür Playlist-Items fehlt noch User-OAuth: `python scripts/spotify_setup.py auth-url`."
            )
        embed = discord.Embed(title="🟢 Spotify Integration", description=description, color=SPOTIFY_GREEN)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def _shuffle_command(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None or not await self.suite._require_queue_access(interaction):
            return
        queue = self.suite.queues[interaction.guild_id]
        items = list(queue)
        if len(items) < 2:
            await interaction.response.send_message("Zum Mischen sind mindestens 2 Queue-Titel nötig.", ephemeral=True)
            return
        random.shuffle(items)
        queue.clear()
        queue.extend(items)
        await self._persist_runtime_impl(interaction.guild_id)
        await interaction.response.send_message(f"🔀 **{len(items)}** Spotify-Titel gemischt.", ephemeral=True)

    @app_commands.describe(position="1-basierte Position in der Spotify-Queue")
    async def _remove_command(
        self,
        interaction: discord.Interaction,
        position: app_commands.Range[int, 1, MAX_QUEUE_PER_GUILD],
    ) -> None:
        if interaction.guild_id is None or not await self.suite._require_queue_access(interaction):
            return
        queue = self.suite.queues[interaction.guild_id]
        items = list(queue)
        index = int(position) - 1
        if index >= len(items):
            await interaction.response.send_message("Diese Queue-Position existiert nicht.", ephemeral=True)
            return
        removed = items.pop(index)
        queue.clear()
        queue.extend(items)
        await self._persist_runtime_impl(interaction.guild_id)
        await interaction.response.send_message(f"🗑️ Entfernt: **{removed.display_title}**", ephemeral=True)

    @app_commands.describe(von="Aktuelle 1-basierte Position", nach="Neue 1-basierte Position")
    async def _move_command(
        self,
        interaction: discord.Interaction,
        von: app_commands.Range[int, 1, MAX_QUEUE_PER_GUILD],
        nach: app_commands.Range[int, 1, MAX_QUEUE_PER_GUILD],
    ) -> None:
        if interaction.guild_id is None or not await self.suite._require_queue_access(interaction):
            return
        queue = self.suite.queues[interaction.guild_id]
        items = list(queue)
        source = int(von) - 1
        target = int(nach) - 1
        if source >= len(items) or target >= len(items):
            await interaction.response.send_message("Eine der Queue-Positionen existiert nicht.", ephemeral=True)
            return
        item = items.pop(source)
        items.insert(target, item)
        queue.clear()
        queue.extend(items)
        await self._persist_runtime_impl(interaction.guild_id)
        await interaction.response.send_message(
            f"↕️ **{item.display_title}**: Position {int(von)} → {int(nach)}",
            ephemeral=True,
        )

    async def _setup_command(self, interaction: discord.Interaction) -> None:
        redirect = self._settings_value("spotify_redirect_uri", "http://127.0.0.1:8765/callback")
        text = (
            "Spotify erstellt Client ID/Secret in deinem Developer-Account; der Bot kann diese Werte nicht selbst erfinden.\n\n"
            "Auf dem Pi:\n"
            "```bash\n"
            "cd ~/services/Raspberry-Bot\n"
            "python scripts/spotify_setup.py credentials\n"
            "python scripts/spotify_setup.py auth-url\n"
            "# danach die komplette Callback-URL kopieren:\n"
            "python scripts/spotify_setup.py exchange '<CALLBACK-URL>'\n"
            "sudo systemctl restart raspberry-bot\n"
            "```\n"
            f"Redirect URI, die in Spotify exakt eingetragen sein muss: `{redirect}`\n\n"
            "Der Helper zeigt Geheimnisse nie im Discord an und speichert sie nur lokal in `.env`."
        )
        embed = discord.Embed(title="🟢 Spotify Setup", description=text, color=SPOTIFY_GREEN)
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SpotifyPlaylistPlus(bot))
