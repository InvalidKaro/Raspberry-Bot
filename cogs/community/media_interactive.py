from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import UTC, datetime
from typing import Any

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

from cogs.community import media_interactive_base as _base
from cogs.community.media_interactive_base import *  # noqa: F403
from services.radio_metadata import fetch_radio_metadata

logger = logging.getLogger(__name__)

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
CREATE TABLE IF NOT EXISTS radio_panel_config(
    guild_id INTEGER PRIMARY KEY,
    channel_id INTEGER NOT NULL,
    message_id INTEGER,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

_PERSIST_KEYS = (
    "active",
    "station_name",
    "stream_title",
    "artist",
    "track",
    "genre",
    "homepage",
    "stream_name",
    "stream_genre",
    "bitrate_kbps",
    "codec",
    "content_type",
    "metadata_supported",
    "last_error",
)


def _safe_discord_text(value: object, limit: int = 500) -> str:
    text = " ".join(str(value or "").replace("\x00", " ").split()).strip()[:limit]
    return discord.utils.escape_mentions(discord.utils.escape_markdown(text))


def _elapsed_text(seconds: int | float) -> str:
    value = max(0, int(seconds))
    hours, remainder = divmod(value, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def _inactive_payload(guild_id: int) -> dict[str, Any]:
    return {
        "guild_id": guild_id,
        "active": False,
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
        "metadata_supported": False,
        "last_error": "",
        "checked_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
    }


class RadioMetadataRuntime(commands.Cog):
    """Pi-friendly radio metadata and automatic persistent panel runtime."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.session: aiohttp.ClientSession | None = None
        self.next_probe_at: dict[int, float] = {}
        self.persisted_state: dict[int, str] = {}
        self.panel_render_state: dict[int, str] = {}
        self.panel_messages: dict[int, discord.Message] = {}
        self.panel_locks: dict[int, asyncio.Lock] = {}

    async def cog_load(self) -> None:
        await self.bot.database.connection.executescript(RADIO_METADATA_SCHEMA)
        await self.bot.database.connection.commit()
        if not hasattr(self.bot, "radio_metadata_cache"):
            self.bot.radio_metadata_cache = {}
        self.session = aiohttp.ClientSession()
        self.radio_metadata_loop.start()
        self.radio_panel_loop.start()

    async def cog_unload(self) -> None:
        self.radio_metadata_loop.cancel()
        self.radio_panel_loop.cancel()
        if self.session is not None and not self.session.closed:
            await self.session.close()
        self.session = None
        self.panel_messages.clear()
        self.panel_render_state.clear()
        self.panel_locks.clear()

    def _panel_lock(self, guild_id: int) -> asyncio.Lock:
        lock = self.panel_locks.get(guild_id)
        if lock is None:
            lock = asyncio.Lock()
            self.panel_locks[guild_id] = lock
        return lock

    def _radio_state(self, guild: discord.Guild) -> tuple[Any | None, discord.VoiceClient | None, bool]:
        voice_cog = self.bot.get_cog("VoiceSuite")
        state = getattr(voice_cog, "states", {}).get(guild.id) if voice_cog else None
        voice = guild.voice_client
        active = bool(
            state
            and str(getattr(state, "kind", "") or "").lower() == "radio"
            and voice
            and voice.is_connected()
            and (voice.is_playing() or voice.is_paused())
        )
        return state, voice, active

    async def _persist(self, guild_id: int, payload: dict[str, Any]) -> None:
        stable = {key: payload.get(key) for key in _PERSIST_KEYS}
        encoded = json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if self.persisted_state.get(guild_id) == encoded:
            return
        self.persisted_state[guild_id] = encoded

        await self.bot.database.execute(
            """
            INSERT INTO radio_runtime_metadata(
                guild_id,active,station_name,stream_title,artist,track,genre,homepage,
                stream_name,stream_genre,bitrate_kbps,codec,content_type,
                metadata_supported,last_error,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
            ON CONFLICT(guild_id) DO UPDATE SET
                active=excluded.active,
                station_name=excluded.station_name,
                stream_title=excluded.stream_title,
                artist=excluded.artist,
                track=excluded.track,
                genre=excluded.genre,
                homepage=excluded.homepage,
                stream_name=excluded.stream_name,
                stream_genre=excluded.stream_genre,
                bitrate_kbps=excluded.bitrate_kbps,
                codec=excluded.codec,
                content_type=excluded.content_type,
                metadata_supported=excluded.metadata_supported,
                last_error=excluded.last_error,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                guild_id,
                int(bool(payload.get("active"))),
                str(payload.get("station_name") or "")[:180],
                str(payload.get("stream_title") or "")[:500],
                str(payload.get("artist") or "")[:220],
                str(payload.get("track") or "")[:300],
                str(payload.get("genre") or "")[:160],
                str(payload.get("homepage") or "")[:1000],
                str(payload.get("stream_name") or "")[:220],
                str(payload.get("stream_genre") or "")[:160],
                payload.get("bitrate_kbps"),
                str(payload.get("codec") or "")[:80],
                str(payload.get("content_type") or "")[:120],
                int(bool(payload.get("metadata_supported"))),
                str(payload.get("last_error") or "")[:500],
            ),
        )

    async def _publish(self, guild_id: int, payload: dict[str, Any]) -> None:
        cache = getattr(self.bot, "radio_metadata_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            self.bot.radio_metadata_cache = cache
        cache[guild_id] = payload
        await self._persist(guild_id, payload)

    async def _update_guild(self, guild: discord.Guild) -> None:
        guild_id = guild.id
        state, _, active = self._radio_state(guild)
        if not active:
            self.next_probe_at.pop(guild_id, None)
            await self._publish(guild_id, _inactive_payload(guild_id))
            return

        station_name = str(getattr(state, "title", "") or getattr(state, "source_name", "") or "").strip()
        row = await self.bot.database.fetchone(
            """
            SELECT name,stream_url,COALESCE(genre,'') genre,COALESCE(homepage,'') homepage
            FROM voice_radio_stations
            WHERE guild_id=? AND lower(name)=lower(?) AND enabled=1
            """,
            (guild_id, station_name),
        )

        previous = dict(getattr(self.bot, "radio_metadata_cache", {}).get(guild_id) or {})
        same_station = str(previous.get("station_name") or "").casefold() == station_name.casefold()
        now = time.monotonic()

        if row is None:
            payload = {
                **_inactive_payload(guild_id),
                "active": True,
                "station_name": station_name,
                "last_error": "Station stream URL not found in voice_radio_stations",
            }
            await self._publish(guild_id, payload)
            return

        if same_station and now < self.next_probe_at.get(guild_id, 0.0):
            previous["active"] = True
            previous["checked_at"] = datetime.now(UTC).replace(microsecond=0).isoformat()
            getattr(self.bot, "radio_metadata_cache", {})[guild_id] = previous
            return

        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()

        metadata = await fetch_radio_metadata(self.session, str(row["stream_url"]))
        preserve_previous = same_station and bool(previous)

        stream_title = metadata.stream_title or (str(previous.get("stream_title") or "") if preserve_previous else "")
        artist = metadata.artist or (str(previous.get("artist") or "") if preserve_previous else "")
        track = metadata.track or (str(previous.get("track") or "") if preserve_previous else "")
        stream_name = metadata.stream_name or (str(previous.get("stream_name") or "") if preserve_previous else "")
        stream_genre = metadata.stream_genre or (str(previous.get("stream_genre") or "") if preserve_previous else "")
        bitrate = metadata.bitrate_kbps if metadata.bitrate_kbps is not None else (previous.get("bitrate_kbps") if preserve_previous else None)
        codec = metadata.codec or (str(previous.get("codec") or "") if preserve_previous else "")
        content_type = metadata.content_type or (str(previous.get("content_type") or "") if preserve_previous else "")

        payload = {
            "guild_id": guild_id,
            "active": True,
            "station_name": str(row["name"]),
            "stream_title": stream_title,
            "artist": artist,
            "track": track,
            "genre": str(row["genre"] or stream_genre or ""),
            "homepage": str(row["homepage"] or ""),
            "stream_name": stream_name,
            "stream_genre": stream_genre,
            "bitrate_kbps": bitrate,
            "codec": codec,
            "content_type": content_type,
            "metadata_supported": bool(metadata.metadata_supported),
            "last_error": metadata.error,
            "checked_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        }
        await self._publish(guild_id, payload)

        if metadata.error:
            self.next_probe_at[guild_id] = now + 30
        elif not metadata.metadata_supported:
            self.next_probe_at[guild_id] = now + 60
        else:
            self.next_probe_at[guild_id] = now + 10

    async def _panel_config(self, guild_id: int):
        return await self.bot.database.fetchone(
            "SELECT channel_id,message_id FROM radio_panel_config WHERE guild_id=?",
            (guild_id,),
        )

    async def _set_panel_message_id(self, guild_id: int, message_id: int | None) -> None:
        await self.bot.database.execute(
            "UPDATE radio_panel_config SET message_id=?,updated_at=CURRENT_TIMESTAMP WHERE guild_id=?",
            (message_id, guild_id),
        )

    async def _load_stations(self, guild_id: int) -> list[dict[str, Any]]:
        rows = await self.bot.database.fetchall(
            "SELECT name,stream_url,COALESCE(genre,'') genre,COALESCE(homepage,'') homepage "
            "FROM voice_radio_stations WHERE guild_id=? AND enabled=1 ORDER BY name COLLATE NOCASE LIMIT 25",
            (guild_id,),
        )
        return [dict(row) for row in rows]

    async def _delete_panel_message(
        self,
        guild: discord.Guild,
        channel_id: int,
        message_id: int | None,
    ) -> None:
        cached = self.panel_messages.pop(guild.id, None)
        self.panel_render_state.pop(guild.id, None)
        if message_id is None and cached is None:
            return

        message = cached if cached is not None and (message_id is None or cached.id == message_id) else None
        channel = guild.get_channel(channel_id)
        if message is None and message_id is not None and isinstance(channel, discord.TextChannel):
            try:
                message = await channel.fetch_message(message_id)
            except discord.NotFound:
                message = None
            except discord.Forbidden:
                logger.warning("No permission to fetch radio panel message %s in guild %s", message_id, guild.id)
                message = None
            except discord.HTTPException as exc:
                logger.warning("Failed to fetch radio panel message %s: %s", message_id, exc)
                message = None

        if message is not None:
            try:
                await message.delete()
            except discord.NotFound:
                pass
            except discord.Forbidden:
                logger.warning("No permission to delete radio panel message %s in guild %s", message.id, guild.id)
            except discord.HTTPException as exc:
                logger.warning("Failed to delete radio panel message %s: %s", message.id, exc)

    def _panel_signature(
        self,
        guild: discord.Guild,
        state: Any,
        voice: discord.VoiceClient,
        station_name: str,
        index: int,
    ) -> str:
        metadata = dict(getattr(self.bot, "radio_metadata_cache", {}).get(guild.id) or {})
        elapsed = max(0, int(time.monotonic() - float(getattr(state, "started_at", time.monotonic()))))
        payload = {
            "station": station_name,
            "index": index,
            "paused": bool(voice.is_paused()),
            "volume": int(getattr(state, "volume", 65) or 65),
            "voice_channel": getattr(getattr(voice, "channel", None), "id", None),
            "artist": str(metadata.get("artist") or ""),
            "track": str(metadata.get("track") or ""),
            "stream_title": str(metadata.get("stream_title") or ""),
            "bitrate_kbps": metadata.get("bitrate_kbps"),
            "codec": str(metadata.get("codec") or ""),
            "metadata_supported": bool(metadata.get("metadata_supported")),
            "elapsed_bucket": elapsed // 30,
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    async def _reconcile_panel(self, guild: discord.Guild) -> None:
        async with self._panel_lock(guild.id):
            config = await self._panel_config(guild.id)
            if config is None:
                return

            channel_id = int(config["channel_id"])
            message_id = int(config["message_id"]) if config["message_id"] is not None else None
            channel = guild.get_channel(channel_id)
            if not isinstance(channel, discord.TextChannel):
                logger.warning("Configured radio panel channel %s is unavailable in guild %s", channel_id, guild.id)
                return

            state, voice, active = self._radio_state(guild)
            if not active or state is None or voice is None:
                if message_id is not None or guild.id in self.panel_messages:
                    await self._delete_panel_message(guild, channel_id, message_id)
                    await self._set_panel_message_id(guild.id, None)
                return

            stations = await self._load_stations(guild.id)
            if not stations:
                logger.warning("Radio is active but no enabled stations exist in guild %s", guild.id)
                return

            current_name = str(getattr(state, "title", "") or getattr(state, "source_name", "") or "").strip()
            index = next(
                (
                    i
                    for i, station in enumerate(stations)
                    if str(station.get("name") or "").casefold() == current_name.casefold()
                ),
                0,
            )
            station_name = str(stations[index].get("name") or current_name)
            signature = self._panel_signature(guild, state, voice, station_name, index)
            view = _base.RadioPanelView(self.bot, guild.id, stations, index)
            embed = view.embed(guild)

            message = self.panel_messages.get(guild.id)
            if message is not None and (message.id != message_id or message.channel.id != channel_id):
                self.panel_messages.pop(guild.id, None)
                message = None

            if message is None and message_id is not None:
                try:
                    message = await channel.fetch_message(message_id)
                except discord.NotFound:
                    message = None
                    await self._set_panel_message_id(guild.id, None)
                    message_id = None
                except discord.Forbidden:
                    logger.warning("No permission to fetch radio panel in %s", channel.mention)
                    return
                except discord.HTTPException as exc:
                    logger.warning("Failed to fetch radio panel in guild %s: %s", guild.id, exc)
                    return

            if message is None:
                try:
                    message = await channel.send(
                        embed=embed,
                        view=view,
                        file=_base._radio_cover(),
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                except discord.Forbidden:
                    logger.warning("No permission to create radio panel in %s", channel.mention)
                    return
                except discord.HTTPException as exc:
                    logger.warning("Failed to create radio panel in guild %s: %s", guild.id, exc)
                    return
                self.panel_messages[guild.id] = message
                self.panel_render_state[guild.id] = signature
                await self._set_panel_message_id(guild.id, message.id)
                return

            self.panel_messages[guild.id] = message
            if self.panel_render_state.get(guild.id) == signature:
                return

            try:
                await message.edit(
                    embed=embed,
                    view=view,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except discord.NotFound:
                self.panel_messages.pop(guild.id, None)
                self.panel_render_state.pop(guild.id, None)
                await self._set_panel_message_id(guild.id, None)
                return
            except discord.Forbidden:
                logger.warning("No permission to update radio panel %s in guild %s", message.id, guild.id)
                return
            except discord.HTTPException as exc:
                logger.warning("Failed to update radio panel %s: %s", message.id, exc)
                return

            self.panel_render_state[guild.id] = signature

    @app_commands.command(
        name="radiopanelchannel",
        description="Legt den dedizierten Kanal für das automatische Live-Radio-Panel fest.",
    )
    @app_commands.describe(kanal="Textkanal, in dem das Radio-Panel während eines Streams erscheinen soll")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def radiopanelchannel(self, interaction: discord.Interaction, kanal: discord.TextChannel) -> None:
        if interaction.guild is None or interaction.guild_id is None:
            return

        member = interaction.guild.me
        if member is None and self.bot.user is not None:
            member = interaction.guild.get_member(self.bot.user.id)
        if member is None:
            await interaction.response.send_message("Bot-Mitglied konnte nicht aufgelöst werden.", ephemeral=True)
            return

        permissions = kanal.permissions_for(member)
        missing: list[str] = []
        if not permissions.view_channel:
            missing.append("Kanal ansehen")
        if not permissions.send_messages:
            missing.append("Nachrichten senden")
        if not permissions.embed_links:
            missing.append("Links einbetten")
        if not permissions.attach_files:
            missing.append("Dateien anhängen")
        if not permissions.read_message_history:
            missing.append("Nachrichtenverlauf lesen")
        if missing:
            await interaction.response.send_message(
                f"Mir fehlen in {kanal.mention}: **{', '.join(missing)}**.",
                ephemeral=True,
            )
            return

        old = await self._panel_config(interaction.guild_id)
        old_channel_id = int(old["channel_id"]) if old is not None else None
        old_message_id = int(old["message_id"]) if old is not None and old["message_id"] is not None else None
        keep_message_id = old_message_id if old_channel_id == kanal.id else None

        if old_channel_id is not None and old_channel_id != kanal.id:
            await self._delete_panel_message(interaction.guild, old_channel_id, old_message_id)

        await self.bot.database.execute(
            """
            INSERT INTO radio_panel_config(guild_id,channel_id,message_id,updated_at)
            VALUES(?,?,?,CURRENT_TIMESTAMP)
            ON CONFLICT(guild_id) DO UPDATE SET
                channel_id=excluded.channel_id,
                message_id=excluded.message_id,
                updated_at=CURRENT_TIMESTAMP
            """,
            (interaction.guild_id, kanal.id, keep_message_id),
        )
        self.panel_render_state.pop(interaction.guild_id, None)
        if keep_message_id is None:
            self.panel_messages.pop(interaction.guild_id, None)

        await interaction.response.send_message(
            f"📻 Automatisches Radio-Panel ist jetzt fest auf {kanal.mention} gesetzt. "
            "Es erscheint beim Start eines Radiosenders und wird nach dem Stop/Disconnect wieder gelöscht.",
            ephemeral=True,
        )
        asyncio.create_task(self._reconcile_panel(interaction.guild))

    @tasks.loop(seconds=10)
    async def radio_metadata_loop(self) -> None:
        results = await asyncio.gather(
            *(self._update_guild(guild) for guild in self.bot.guilds),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, Exception):
                logger.warning("Radio metadata refresh failed: %s", result)

    @radio_metadata_loop.before_loop
    async def before_radio_metadata_loop(self) -> None:
        await self.bot.wait_until_ready()

    @tasks.loop(seconds=3)
    async def radio_panel_loop(self) -> None:
        results = await asyncio.gather(
            *(self._reconcile_panel(guild) for guild in self.bot.guilds),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, Exception):
                logger.warning("Radio panel reconciliation failed: %s", result)

    @radio_panel_loop.before_loop
    async def before_radio_panel_loop(self) -> None:
        await self.bot.wait_until_ready()


class _RadioMetadataRefreshButton(discord.ui.Button):
    def __init__(self, panel) -> None:
        self.panel = panel
        super().__init__(
            label="Refresh",
            emoji="🔄",
            style=discord.ButtonStyle.secondary,
            custom_id="homepi:radio:metadata-refresh",
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(embed=self.panel.embed(interaction.guild), view=self.panel)


_ORIGINAL_RADIO_INIT = getattr(
    _base.RadioPanelView,
    "_homepi_radio_metadata_original_init",
    _base.RadioPanelView.__init__,
)


def _enhanced_radio_init(self, bot: commands.Bot, guild_id: int, stations: list[dict], index: int = 0) -> None:
    _ORIGINAL_RADIO_INIT(self, bot, guild_id, stations, index)
    self.timeout = None
    if not any(isinstance(child, _RadioMetadataRefreshButton) for child in self.children):
        self.add_item(_RadioMetadataRefreshButton(self))


_ORIGINAL_RADIO_EMBED = getattr(
    _base.RadioPanelView,
    "_homepi_radio_metadata_original_embed",
    _base.RadioPanelView.embed,
)


def _enhanced_radio_embed(self, guild: discord.Guild | None) -> discord.Embed:
    embed = _ORIGINAL_RADIO_EMBED(self, guild)
    station = self.station
    voice_cog = self.bot.get_cog("VoiceSuite")
    state = getattr(voice_cog, "states", {}).get(self.guild_id) if voice_cog else None
    voice = guild.voice_client if guild else None
    is_live = bool(
        state
        and str(getattr(state, "kind", "") or "").lower() == "radio"
        and str(getattr(state, "title", "") or "").casefold() == str(station["name"]).casefold()
        and voice
        and (voice.is_playing() or voice.is_paused())
    )
    if not is_live:
        return embed

    metadata = dict(getattr(self.bot, "radio_metadata_cache", {}).get(self.guild_id) or {})
    same_station = str(metadata.get("station_name") or "").casefold() == str(station["name"]).casefold()
    if same_station:
        artist = _safe_discord_text(metadata.get("artist"), 180)
        track = _safe_discord_text(metadata.get("track"), 220)
        stream_title = _safe_discord_text(metadata.get("stream_title"), 380)
        if artist and track:
            now_playing = f"**{artist}**\n{track}"
        elif stream_title:
            now_playing = stream_title
        elif track:
            now_playing = track
        else:
            now_playing = "Live-Stream · keine Titelmetadaten"
        embed.insert_field_at(1, name="🎵 Jetzt läuft", value=now_playing[:1024], inline=False)

        stream_parts: list[str] = []
        bitrate = metadata.get("bitrate_kbps")
        if bitrate:
            stream_parts.append(f"{int(bitrate)} kbps")
        codec = _safe_discord_text(metadata.get("codec"), 40)
        if codec:
            stream_parts.append(codec)
        if stream_parts:
            embed.add_field(name="Stream", value=f"`{' · '.join(stream_parts)}`", inline=True)

    if state:
        elapsed = max(0, int(time.monotonic() - float(getattr(state, "started_at", time.monotonic()))))
        embed.add_field(name="Laufzeit", value=f"`{_elapsed_text(elapsed)}`", inline=True)

    embed.set_footer(text="Live-Metadaten · ◀ ▶ Sender · ⏯ Pause · 🔊 Lautstärke")
    return embed


def _install_radio_panel_patch() -> None:
    if not hasattr(_base.RadioPanelView, "_homepi_radio_metadata_original_embed"):
        _base.RadioPanelView._homepi_radio_metadata_original_embed = _ORIGINAL_RADIO_EMBED
    if not hasattr(_base.RadioPanelView, "_homepi_radio_metadata_original_init"):
        _base.RadioPanelView._homepi_radio_metadata_original_init = _ORIGINAL_RADIO_INIT
    _base.RadioPanelView.__init__ = _enhanced_radio_init
    _base.RadioPanelView.embed = _enhanced_radio_embed


def _restore_radio_panel_patch() -> None:
    original_embed = getattr(_base.RadioPanelView, "_homepi_radio_metadata_original_embed", None)
    original_init = getattr(_base.RadioPanelView, "_homepi_radio_metadata_original_init", None)
    if original_embed is not None:
        _base.RadioPanelView.embed = original_embed
    if original_init is not None:
        _base.RadioPanelView.__init__ = original_init


async def setup(bot: commands.Bot) -> None:
    _install_radio_panel_patch()
    await bot.add_cog(_base.MediaInteractive(bot))
    await bot.add_cog(RadioMetadataRuntime(bot))


async def teardown(bot: commands.Bot) -> None:
    del bot
    _restore_radio_panel_patch()
