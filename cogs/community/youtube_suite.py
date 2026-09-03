from __future__ import annotations

import asyncio
import logging
import os
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands, tasks

try:
    import yt_dlp
except ImportError:  # pragma: no cover - handled at runtime
    yt_dlp = None

logger = logging.getLogger(__name__)

MAX_QUEUE_PER_GUILD = 25
DEFAULT_VOLUME = 65


@dataclass(slots=True)
class YouTubeTrack:
    query: str
    title: str
    webpage_url: str
    requested_by: int
    duration: int | None = None


@dataclass(slots=True)
class ResolvedTrack:
    track: YouTubeTrack
    stream_url: str
    user_agent: str | None = None


def _duration_text(seconds: int | None) -> str:
    if not seconds:
        return "Live/Unbekannt"
    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def _card(title: str, text: str, color: int = 0xFF0000) -> discord.Embed:
    embed = discord.Embed(title=title, description=text, color=color)
    embed.set_footer(text="Private YouTube Voice · Owner startet · freigegebene Mods dürfen Songs hinzufügen")
    return embed


class YouTubeSuite(
    commands.GroupCog,
    group_name="youtube",
    group_description="Private YouTube-Musikqueue für Voice",
):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.queues: dict[int, deque[YouTubeTrack]] = defaultdict(deque)
        self.current: dict[int, YouTubeTrack] = {}
        self.session_active: set[int] = set()
        self.starting: set[int] = set()
        self.queue_guard.start()

    async def cog_load(self) -> None:
        await self.bot.database.execute(
            """
            CREATE TABLE IF NOT EXISTS youtube_queue_mods(
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                added_by INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(guild_id,user_id)
            )
            """
        )

    def cog_unload(self) -> None:
        self.queue_guard.cancel()

    def _is_owner(self, user_id: int) -> bool:
        return user_id in set(self.bot.settings.owner_ids)

    async def _is_queue_mod(self, guild_id: int, user_id: int) -> bool:
        if self._is_owner(user_id):
            return True
        row = await self.bot.database.fetchone(
            "SELECT 1 FROM youtube_queue_mods WHERE guild_id=? AND user_id=?",
            (guild_id, user_id),
        )
        return row is not None

    async def _require_owner(self, interaction: discord.Interaction) -> bool:
        if self._is_owner(interaction.user.id):
            return True
        await interaction.response.send_message(
            "Nur der Bot-Owner darf die YouTube-Wiedergabe steuern.",
            ephemeral=True,
        )
        return False

    async def _require_queue_access(self, interaction: discord.Interaction) -> bool:
        if interaction.guild_id is None:
            return False
        if await self._is_queue_mod(interaction.guild_id, interaction.user.id):
            return True
        await interaction.response.send_message(
            "Die private YouTube-Queue ist für dich nicht freigeschaltet. Der Bot-Owner kann dich mit `/media youtube mod` hinzufügen.",
            ephemeral=True,
        )
        return False

    def _ydl_options(self) -> dict[str, Any]:
        options: dict[str, Any] = {
            "format": "bestaudio/best",
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "skip_download": True,
            "default_search": "ytsearch1",
            "socket_timeout": 15,
            "cachedir": False,
            "extract_flat": False,
        }
        cookie_file = os.getenv("YTDLP_COOKIES_FILE", "").strip()
        if cookie_file:
            options["cookiefile"] = cookie_file
        return options

    def _extract_sync(self, query: str, requested_by: int) -> ResolvedTrack:
        if yt_dlp is None:
            raise RuntimeError("Das Python-Paket `yt-dlp` ist nicht installiert.")
        value = " ".join(query.split()).strip()
        if not value:
            raise ValueError("YouTube-Link oder Suchbegriff fehlt.")
        target = value if "://" in value else f"ytsearch1:{value}"
        with yt_dlp.YoutubeDL(self._ydl_options()) as ydl:
            info = ydl.extract_info(target, download=False)
        if not info:
            raise ValueError("Kein YouTube-Ergebnis gefunden.")
        if info.get("entries"):
            entries = [entry for entry in info.get("entries") or [] if entry]
            if not entries:
                raise ValueError("Kein abspielbares YouTube-Ergebnis gefunden.")
            info = entries[0]
        stream_url = str(info.get("url") or "").strip()
        if not stream_url.startswith(("https://", "http://")):
            raise ValueError("yt-dlp konnte keine abspielbare Audioquelle auflösen.")
        title = str(info.get("title") or value).strip()[:180]
        webpage_url = str(info.get("webpage_url") or info.get("original_url") or value).strip()[:1000]
        duration_raw = info.get("duration")
        try:
            duration = int(duration_raw) if duration_raw is not None else None
        except (TypeError, ValueError):
            duration = None
        headers = info.get("http_headers") or {}
        user_agent = str(headers.get("User-Agent") or "").strip() or None
        return ResolvedTrack(
            track=YouTubeTrack(
                query=value,
                title=title,
                webpage_url=webpage_url,
                requested_by=requested_by,
                duration=duration,
            ),
            stream_url=stream_url,
            user_agent=user_agent,
        )

    async def _resolve(self, query: str, requested_by: int) -> ResolvedTrack:
        return await asyncio.wait_for(
            asyncio.to_thread(self._extract_sync, query, requested_by),
            timeout=35,
        )

    def _voice_cog(self):
        voice = self.bot.get_cog("VoiceSuite")
        if voice is None:
            raise RuntimeError("VoiceSuite ist nicht geladen.")
        return voice

    def _audio_source(self, resolved: ResolvedTrack) -> discord.AudioSource:
        before = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -rw_timeout 15000000"
        if resolved.user_agent:
            safe_agent = resolved.user_agent.replace('"', "")[:240]
            before += f' -user_agent "{safe_agent}"'
        return discord.FFmpegPCMAudio(
            resolved.stream_url,
            before_options=before,
            options="-vn -loglevel error",
        )

    async def _start_resolved(
        self,
        interaction: discord.Interaction,
        resolved: ResolvedTrack,
        *,
        volume: int = DEFAULT_VOLUME,
    ) -> None:
        voice_cog = self._voice_cog()
        voice = await voice_cog._get_voice(interaction)
        if voice is None:
            raise ValueError("Du musst in einem Voice-Channel sein.")
        gid = int(interaction.guild_id or 0)
        await voice_cog._start_on_voice(
            voice,
            self._audio_source(resolved),
            guild_id=gid,
            title=resolved.track.title,
            kind="YouTube",
            started_by=resolved.track.requested_by,
            source_name=resolved.track.webpage_url,
            volume=max(10, min(120, int(volume))),
        )
        self.current[gid] = resolved.track
        self.session_active.add(gid)

    async def _start_queued(self, guild_id: int, track: YouTubeTrack) -> None:
        if guild_id in self.starting:
            return
        self.starting.add(guild_id)
        try:
            guild = self.bot.get_guild(guild_id)
            if guild is None or guild.voice_client is None or not guild.voice_client.is_connected():
                self.session_active.discard(guild_id)
                return
            resolved = await self._resolve(track.query, track.requested_by)
            voice_cog = self._voice_cog()
            await voice_cog._start_on_voice(
                guild.voice_client,
                self._audio_source(resolved),
                guild_id=guild_id,
                title=resolved.track.title,
                kind="YouTube",
                started_by=track.requested_by,
                source_name=resolved.track.webpage_url,
                volume=DEFAULT_VOLUME,
            )
            self.current[guild_id] = resolved.track
        except Exception as exc:
            logger.warning("Could not start queued YouTube item in guild %s: %s", guild_id, exc)
            self.current.pop(guild_id, None)
        finally:
            self.starting.discard(guild_id)

    @app_commands.command(name="play", description="Owner-only: startet einen YouTube-Song im aktuellen Voice-Channel.")
    @app_commands.describe(suche="YouTube-Link oder Suchbegriff", lautstaerke="10 bis 120 Prozent")
    async def play(
        self,
        interaction: discord.Interaction,
        suche: str,
        lautstaerke: app_commands.Range[int, 10, 120] = DEFAULT_VOLUME,
    ) -> None:
        if interaction.guild_id is None or not await self._require_owner(interaction):
            return
        if yt_dlp is None:
            await interaction.response.send_message("`yt-dlp` fehlt auf dem Pi.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            resolved = await self._resolve(suche, interaction.user.id)
            await self._start_resolved(interaction, resolved, volume=int(lautstaerke))
        except (ValueError, RuntimeError, asyncio.TimeoutError) as exc:
            await interaction.followup.send(f"YouTube konnte nicht gestartet werden: {exc}", ephemeral=True)
            return
        except Exception as exc:
            logger.exception("YouTube play failed")
            await interaction.followup.send(f"YouTube-Wiedergabe fehlgeschlagen: {type(exc).__name__}", ephemeral=True)
            return
        track = resolved.track
        await interaction.followup.send(
            embed=_card(
                "▶️ YouTube",
                f"**{track.title}**\nDauer: **{_duration_text(track.duration)}** · Lautstärke: **{int(lautstaerke)}%**\n\nFreigegebene Mods können jetzt mit `/media youtube add` Songs anhängen.",
            ),
            ephemeral=True,
        )

    @app_commands.command(name="add", description="Fügt einen Song zur privaten YouTube-Queue hinzu.")
    @app_commands.describe(suche="YouTube-Link oder Suchbegriff")
    async def add(self, interaction: discord.Interaction, suche: str) -> None:
        if interaction.guild_id is None or not await self._require_queue_access(interaction):
            return
        queue = self.queues[interaction.guild_id]
        if len(queue) >= MAX_QUEUE_PER_GUILD:
            await interaction.response.send_message(f"Die Queue ist auf {MAX_QUEUE_PER_GUILD} Songs begrenzt.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            resolved = await self._resolve(suche, interaction.user.id)
        except (ValueError, RuntimeError, asyncio.TimeoutError) as exc:
            await interaction.followup.send(f"Song konnte nicht hinzugefügt werden: {exc}", ephemeral=True)
            return
        queue.append(resolved.track)
        active = interaction.guild_id in self.session_active
        suffix = "" if active else "\n\nDie Queue startet erst, wenn der Bot-Owner `/media youtube play` ausführt."
        await interaction.followup.send(
            embed=_card(
                "➕ YouTube Queue",
                f"**{resolved.track.title}**\nPosition: **{len(queue)}** · Dauer: **{_duration_text(resolved.track.duration)}**{suffix}",
                0x5865F2,
            ),
            ephemeral=True,
        )

    @app_commands.command(name="queue", description="Zeigt die private YouTube-Queue.")
    async def queue(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None or not await self._require_queue_access(interaction):
            return
        current = self.current.get(interaction.guild_id)
        queued = list(self.queues.get(interaction.guild_id, ()))
        lines: list[str] = []
        if current:
            lines.append(f"**Jetzt:** {current.title} · {_duration_text(current.duration)}")
        else:
            lines.append("**Jetzt:** —")
        if queued:
            lines.append("")
            for index, track in enumerate(queued[:15], start=1):
                lines.append(f"`{index:02d}` **{track.title}** · {_duration_text(track.duration)} · <@{track.requested_by}>")
        else:
            lines.append("\nQueue leer.")
        await interaction.response.send_message(
            embed=_card("🎵 YouTube Queue", "\n".join(lines), 0x5865F2),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name="skip", description="Owner-only: überspringt den aktuellen YouTube-Song.")
    async def skip(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None or not await self._require_owner(interaction):
            return
        voice = interaction.guild.voice_client if interaction.guild else None
        if voice is None or not (voice.is_playing() or voice.is_paused()):
            await interaction.response.send_message("Aktuell läuft kein Song.", ephemeral=True)
            return
        voice.stop()
        self.current.pop(interaction.guild_id, None)
        await interaction.response.send_message("⏭️ Übersprungen. Nächster Queue-Eintrag startet automatisch.", ephemeral=True)

    @app_commands.command(name="stop", description="Owner-only: stoppt die private YouTube-Session.")
    async def stop(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None or not await self._require_owner(interaction):
            return
        self.session_active.discard(interaction.guild_id)
        self.current.pop(interaction.guild_id, None)
        voice = interaction.guild.voice_client if interaction.guild else None
        if voice and (voice.is_playing() or voice.is_paused()):
            voice.stop()
        await interaction.response.send_message("⏹️ YouTube-Session gestoppt. Die Queue bleibt gespeichert.", ephemeral=True)

    @app_commands.command(name="clear", description="Owner-only: leert die YouTube-Queue.")
    async def clear(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None or not await self._require_owner(interaction):
            return
        count = len(self.queues.get(interaction.guild_id, ()))
        self.queues[interaction.guild_id].clear()
        await interaction.response.send_message(f"🧹 **{count}** Queue-Einträge entfernt.", ephemeral=True)

    @app_commands.command(name="mod", description="Owner-only: erlaubt oder entzieht einem User das Hinzufügen zur YouTube-Queue.")
    @app_commands.describe(mitglied="User für die private Queue", erlauben="True = erlauben, False = entziehen")
    async def mod(self, interaction: discord.Interaction, mitglied: discord.Member, erlauben: bool = True) -> None:
        if interaction.guild_id is None or not await self._require_owner(interaction):
            return
        if mitglied.bot:
            await interaction.response.send_message("Bots werden nicht zur YouTube-Queue freigeschaltet.", ephemeral=True)
            return
        if erlauben:
            await self.bot.database.execute(
                "INSERT OR REPLACE INTO youtube_queue_mods(guild_id,user_id,added_by,created_at) VALUES(?,?,?,CURRENT_TIMESTAMP)",
                (interaction.guild_id, mitglied.id, interaction.user.id),
            )
            text = f"✅ {mitglied.mention} darf jetzt `/media youtube add` und `/media youtube queue` nutzen."
        else:
            await self.bot.database.execute(
                "DELETE FROM youtube_queue_mods WHERE guild_id=? AND user_id=?",
                (interaction.guild_id, mitglied.id),
            )
            text = f"🔒 YouTube-Queue-Zugriff für {mitglied.mention} entfernt."
        await interaction.response.send_message(text, ephemeral=True)

    @app_commands.command(name="mods", description="Owner-only: zeigt freigeschaltete YouTube-Queue-User.")
    async def mods(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None or not await self._require_owner(interaction):
            return
        rows = await self.bot.database.fetchall(
            "SELECT user_id,created_at FROM youtube_queue_mods WHERE guild_id=? ORDER BY created_at",
            (interaction.guild_id,),
        )
        text = "\n".join(f"• <@{row['user_id']}> · {row['created_at']}" for row in rows) or "Keine zusätzlichen User freigeschaltet."
        await interaction.response.send_message(
            embed=_card("🔐 YouTube Queue Mods", text, 0x5865F2),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @tasks.loop(seconds=2)
    async def queue_guard(self) -> None:
        for guild_id in list(self.session_active):
            if guild_id in self.starting:
                continue
            guild = self.bot.get_guild(guild_id)
            voice = guild.voice_client if guild else None
            if voice is None or not voice.is_connected():
                self.session_active.discard(guild_id)
                self.current.pop(guild_id, None)
                continue
            if voice.is_playing() or voice.is_paused():
                continue
            self.current.pop(guild_id, None)
            queue = self.queues.get(guild_id)
            if queue:
                track = queue.popleft()
                await self._start_queued(guild_id, track)
            else:
                self.session_active.discard(guild_id)

    @queue_guard.before_loop
    async def before_queue_guard(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(YouTubeSuite(bot))
