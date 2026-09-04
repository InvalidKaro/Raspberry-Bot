from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from io import BytesIO
from urllib.parse import quote, urlparse

import discord
from discord import app_commands
from discord.ext import commands
from PIL import Image, ImageDraw

from cogs.community.youtube_suite import MAX_QUEUE_PER_GUILD, YouTubeTrack, yt_dlp


RADIO_COLOR = 0x2AA7FF
YOUTUBE_COLOR = 0xFF2449


def _duration_text(seconds: int | None) -> str:
    if not seconds:
        return "Live/Unbekannt"
    value = max(0, int(seconds))
    hours, remainder = divmod(value, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def _radio_cover() -> discord.File:
    size = 420
    image = Image.new("RGB", (size, size), (5, 9, 15))
    draw = ImageDraw.Draw(image)
    blue = (42, 167, 255)
    dim = (15, 72, 112)
    panel = (11, 21, 32)

    for radius, color in ((170, (7, 26, 40)), (138, (8, 37, 57)), (105, (8, 49, 76))):
        draw.ellipse(
            (size // 2 - radius, size // 2 - radius, size // 2 + radius, size // 2 + radius),
            outline=color,
            width=5,
        )

    draw.line((102, 118, 164, 45), fill=blue, width=11)
    draw.ellipse((156, 36, 171, 51), fill=blue)
    draw.rounded_rectangle((60, 112, 360, 320), radius=31, fill=panel, outline=blue, width=8)
    draw.rounded_rectangle((91, 147, 245, 208), radius=14, fill=(3, 12, 20), outline=dim, width=4)
    draw.line((111, 177, 224, 177), fill=blue, width=5)
    draw.ellipse((283, 148, 326, 191), outline=blue, width=7)
    draw.ellipse((278, 225, 333, 280), outline=blue, width=8)
    draw.ellipse((298, 204, 312, 218), fill=(49, 196, 141))

    for row in range(4):
        for col in range(7):
            x = 93 + col * 23
            y = 239 + row * 16
            draw.ellipse((x, y, x + 7, y + 7), fill=dim)

    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    buffer.seek(0)
    return discord.File(buffer, filename="homepi-radio.png")


def _station_logo(station: dict) -> str | None:
    homepage = str(station.get("homepage") or "").strip()
    if not homepage.startswith(("https://", "http://")):
        return None
    try:
        parsed = urlparse(homepage)
    except ValueError:
        return None
    if not parsed.hostname:
        return None
    return f"https://www.google.com/s2/favicons?domain={quote(parsed.hostname)}&sz=256"


@dataclass(slots=True)
class SearchResult:
    title: str
    url: str
    duration: int | None
    thumbnail: str | None


class RadioStationSelect(discord.ui.Select):
    def __init__(self, panel: "RadioPanelView") -> None:
        self.panel = panel
        options = []
        for index, station in enumerate(panel.stations[:25]):
            genre = str(station.get("genre") or "").strip()
            options.append(
                discord.SelectOption(
                    label=str(station["name"])[:100],
                    value=str(index),
                    description=(genre or "Internet Radio")[:100],
                    emoji="📻",
                    default=index == panel.index,
                )
            )
        super().__init__(placeholder="Sender auswählen …", min_values=1, max_values=1, options=options, row=2)

    async def callback(self, interaction: discord.Interaction) -> None:
        self.panel.index = max(0, min(len(self.panel.stations) - 1, int(self.values[0])))
        self.panel.refresh_select()
        await interaction.response.edit_message(embed=self.panel.embed(interaction.guild), view=self.panel)


class RadioPanelView(discord.ui.View):
    def __init__(self, bot: commands.Bot, guild_id: int, stations: list[dict], index: int = 0) -> None:
        super().__init__(timeout=900)
        self.bot = bot
        self.guild_id = guild_id
        self.stations = stations
        self.index = max(0, min(len(stations) - 1, index))
        self.refresh_select()

    @property
    def station(self) -> dict:
        return self.stations[self.index]

    def refresh_select(self) -> None:
        for child in list(self.children):
            if isinstance(child, RadioStationSelect):
                self.remove_item(child)
        self.add_item(RadioStationSelect(self))

    def embed(self, guild: discord.Guild | None) -> discord.Embed:
        station = self.station
        voice_cog = self.bot.get_cog("VoiceSuite")
        state = getattr(voice_cog, "states", {}).get(self.guild_id) if voice_cog else None
        voice = guild.voice_client if guild else None
        is_live = bool(
            state
            and str(getattr(state, "kind", "")).lower() == "radio"
            and str(getattr(state, "title", "")).lower() == str(station["name"]).lower()
            and voice
            and (voice.is_playing() or voice.is_paused())
        )
        paused = bool(is_live and voice and voice.is_paused())

        genre = str(station.get("genre") or "").strip() or "Radio"
        status = "⏸️ **PAUSIERT**" if paused else "🔴 **LIVE**" if is_live else "⚪ Bereit zum Starten"
        embed = discord.Embed(
            title=f"📻 {station['name']}",
            description=f"{genre}\n{status}",
            color=RADIO_COLOR if is_live else 0x202936,
        )
        embed.set_author(name="HomePi Radio Station Panel")
        embed.add_field(name="Sender", value=f"**{self.index + 1}/{len(self.stations)}**", inline=True)
        if state and is_live:
            embed.add_field(name="Lautstärke", value=f"**{getattr(state, 'volume', 65)}%**", inline=True)
        if voice and getattr(voice, "channel", None):
            embed.add_field(name="Voice", value=voice.channel.mention, inline=True)

        homepage = str(station.get("homepage") or "").strip()
        if homepage.startswith(("https://", "http://")):
            embed.add_field(name="Website", value=f"[Sender öffnen]({homepage})", inline=False)

        logo = _station_logo(station)
        embed.set_thumbnail(url=logo or "attachment://homepi-radio.png")
        embed.set_footer(text="◀ ▶ durchsuchen · ▶ Play startet den ausgewählten Sender")
        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.guild_id != self.guild_id:
            await interaction.response.send_message("Dieses Radio-Panel gehört zu einem anderen Server.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Play", emoji="▶️", style=discord.ButtonStyle.success, row=0)
    async def play(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not isinstance(interaction.user, discord.Member) or not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message("Du musst zuerst einem Voice-Channel beitreten.", ephemeral=True)
            return
        voice_cog = self.bot.get_cog("VoiceSuite")
        if voice_cog is None:
            await interaction.response.send_message("Voice-System ist nicht geladen.", ephemeral=True)
            return
        await interaction.response.defer()
        try:
            voice = await voice_cog._connect_channel(interaction.guild, interaction.user.voice.channel)
            station = self.station
            await voice_cog._start_on_voice(
                voice,
                voice_cog._remote_source(str(station["stream_url"])),
                guild_id=self.guild_id,
                title=str(station["name"]),
                kind="Radio",
                started_by=interaction.user.id,
                source_name=str(station["name"]),
                volume=65,
            )
            await interaction.message.edit(embed=self.embed(interaction.guild), view=self)
            await interaction.followup.send(f"📻 **{station['name']}** läuft jetzt.", ephemeral=True)
        except Exception as exc:
            await interaction.followup.send(f"Radio konnte nicht gestartet werden: {exc}", ephemeral=True)

    @discord.ui.button(label="Favorit", emoji="⭐", style=discord.ButtonStyle.secondary, row=0)
    async def favorite(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        station = self.station
        await self.bot.database.execute(
            "INSERT OR IGNORE INTO voice_radio_favorites(guild_id,user_id,station_name) VALUES(?,?,?)",
            (self.guild_id, interaction.user.id, str(station["name"])),
        )
        await interaction.response.send_message(f"⭐ **{station['name']}** als Favorit gespeichert.", ephemeral=True)

    @discord.ui.button(label="Pause", emoji="⏯️", style=discord.ButtonStyle.primary, row=0)
    async def pause_resume(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        voice = interaction.guild.voice_client if interaction.guild else None
        if voice is None or not voice.is_connected():
            await interaction.response.send_message("Keine aktive Voice-Session.", ephemeral=True)
            return
        if voice.is_paused():
            voice.resume()
        elif voice.is_playing():
            voice.pause()
        else:
            await interaction.response.send_message("Aktuell läuft nichts.", ephemeral=True)
            return
        await interaction.response.edit_message(embed=self.embed(interaction.guild), view=self)

    @discord.ui.button(label="-10%", emoji="🔉", style=discord.ButtonStyle.secondary, row=0)
    async def quieter(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        voice_cog = self.bot.get_cog("VoiceSuite")
        try:
            voice_cog.set_session_volume(self.guild_id, -10, relative=True)
        except (AttributeError, ValueError) as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await interaction.response.edit_message(embed=self.embed(interaction.guild), view=self)

    @discord.ui.button(label="+10%", emoji="🔊", style=discord.ButtonStyle.secondary, row=0)
    async def louder(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        voice_cog = self.bot.get_cog("VoiceSuite")
        try:
            voice_cog.set_session_volume(self.guild_id, 10, relative=True)
        except (AttributeError, ValueError) as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await interaction.response.edit_message(embed=self.embed(interaction.guild), view=self)

    @discord.ui.button(label="Sender", emoji="◀️", style=discord.ButtonStyle.secondary, row=1)
    async def previous(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.index = (self.index - 1) % len(self.stations)
        self.refresh_select()
        await interaction.response.edit_message(embed=self.embed(interaction.guild), view=self)

    @discord.ui.button(label="Stop", emoji="⏹️", style=discord.ButtonStyle.danger, row=1)
    async def stop(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        voice = interaction.guild.voice_client if interaction.guild else None
        if voice is None or not (voice.is_playing() or voice.is_paused()):
            await interaction.response.send_message("Aktuell läuft nichts.", ephemeral=True)
            return
        voice.stop()
        await interaction.response.edit_message(embed=self.embed(interaction.guild), view=self)

    @discord.ui.button(label="Sender", emoji="▶️", style=discord.ButtonStyle.secondary, row=1)
    async def next(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.index = (self.index + 1) % len(self.stations)
        self.refresh_select()
        await interaction.response.edit_message(embed=self.embed(interaction.guild), view=self)


class YouTubeResultButton(discord.ui.Button):
    def __init__(self, view: "YouTubeSearchView", index: int, action: str) -> None:
        self.search_view = view
        self.index = index
        self.action = action
        if action == "play":
            label, emoji, style = f"Play {index + 1}", "▶️", discord.ButtonStyle.success
        else:
            label, emoji, style = f"Queue {index + 1}", "➕", discord.ButtonStyle.secondary
        super().__init__(label=label, emoji=emoji, style=style, row=index)

    async def callback(self, interaction: discord.Interaction) -> None:
        result = self.search_view.results[self.index]
        yt = self.search_view.bot.get_cog("YouTubeSuite")
        if yt is None:
            await interaction.response.send_message("YouTube-System ist nicht geladen.", ephemeral=True)
            return

        guild_id = interaction.guild_id or 0
        if self.action == "play":
            if not yt._is_owner(interaction.user.id):
                await interaction.response.send_message("Nur der Bot-Owner darf einen YouTube-Titel direkt starten.", ephemeral=True)
                return
            if not isinstance(interaction.user, discord.Member) or not interaction.user.voice or not interaction.user.voice.channel:
                await interaction.response.send_message("Du musst zuerst einem Voice-Channel beitreten.", ephemeral=True)
                return
            await interaction.response.defer(ephemeral=True)
            try:
                resolved = await yt._resolve(result.url, interaction.user.id)
                voice_cog = yt._voice_cog()
                voice = await voice_cog._connect_channel(interaction.guild, interaction.user.voice.channel)
                await voice_cog._start_on_voice(
                    voice,
                    yt._audio_source(resolved),
                    guild_id=guild_id,
                    title=resolved.track.title,
                    kind="YouTube",
                    started_by=interaction.user.id,
                    source_name=resolved.track.webpage_url,
                    volume=65,
                )
                yt.current[guild_id] = resolved.track
                yt.session_active.add(guild_id)
                await interaction.followup.send(f"▶️ **{resolved.track.title}** gestartet.", ephemeral=True)
            except Exception as exc:
                await interaction.followup.send(f"YouTube konnte nicht gestartet werden: {exc}", ephemeral=True)
            return

        if not await yt._is_queue_mod(guild_id, interaction.user.id):
            await interaction.response.send_message("Du bist für die private YouTube-Queue nicht freigeschaltet.", ephemeral=True)
            return
        queue = yt.queues[guild_id]
        if len(queue) >= MAX_QUEUE_PER_GUILD:
            await interaction.response.send_message(f"Die Queue ist auf {MAX_QUEUE_PER_GUILD} Songs begrenzt.", ephemeral=True)
            return
        queue.append(
            YouTubeTrack(
                query=result.url,
                title=result.title,
                webpage_url=result.url,
                requested_by=interaction.user.id,
                duration=result.duration,
            )
        )
        await interaction.response.send_message(
            f"➕ **{result.title}** auf Position **{len(queue)}** gesetzt.",
            ephemeral=True,
        )


class YouTubeSearchView(discord.ui.View):
    def __init__(self, bot: commands.Bot, results: list[SearchResult]) -> None:
        super().__init__(timeout=300)
        self.bot = bot
        self.results = results
        for index in range(len(results)):
            self.add_item(YouTubeResultButton(self, index, "play"))
            self.add_item(YouTubeResultButton(self, index, "queue"))


class MediaInteractive(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="radiopanel", description="Öffnet das interaktive HomePi Radio Station Panel.")
    async def radiopanel(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            return
        rows = await self.bot.database.fetchall(
            "SELECT name,stream_url,COALESCE(genre,'') genre,COALESCE(homepage,'') homepage "
            "FROM voice_radio_stations WHERE guild_id=? AND enabled=1 ORDER BY name COLLATE NOCASE LIMIT 25",
            (interaction.guild_id,),
        )
        stations = [dict(row) for row in rows]
        if not stations:
            await interaction.response.send_message("Noch keine Radiosender gespeichert.", ephemeral=True)
            return

        voice_cog = self.bot.get_cog("VoiceSuite")
        state = getattr(voice_cog, "states", {}).get(interaction.guild_id) if voice_cog else None
        current_name = str(getattr(state, "title", "") or "") if state and str(getattr(state, "kind", "")).lower() == "radio" else ""
        index = next((i for i, station in enumerate(stations) if str(station["name"]).lower() == current_name.lower()), 0)
        view = RadioPanelView(self.bot, interaction.guild_id, stations, index)
        await interaction.response.send_message(
            embed=view.embed(interaction.guild),
            view=view,
            file=_radio_cover(),
        )

    def _search_sync(self, query: str) -> list[SearchResult]:
        if yt_dlp is None:
            raise RuntimeError("`yt-dlp` fehlt auf dem Pi.")
        options = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "extract_flat": True,
            "playlistend": 5,
            "socket_timeout": 15,
            "cachedir": False,
        }
        cookie_file = os.getenv("YTDLP_COOKIES_FILE", "").strip()
        if cookie_file:
            options["cookiefile"] = cookie_file
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(f"ytsearch5:{query}", download=False)
        entries = [entry for entry in (info or {}).get("entries", []) if entry][:5]
        results: list[SearchResult] = []
        for entry in entries:
            video_id = str(entry.get("id") or "").strip()
            raw_url = str(entry.get("webpage_url") or entry.get("url") or "").strip()
            url = raw_url if raw_url.startswith(("https://", "http://")) else (f"https://www.youtube.com/watch?v={video_id}" if video_id else "")
            if not url:
                continue
            duration_raw = entry.get("duration")
            try:
                duration = int(duration_raw) if duration_raw is not None else None
            except (TypeError, ValueError):
                duration = None
            thumbnail = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg" if video_id else None
            results.append(
                SearchResult(
                    title=str(entry.get("title") or "YouTube")[:180],
                    url=url[:1000],
                    duration=duration,
                    thumbnail=thumbnail,
                )
            )
        return results

    @app_commands.command(name="youtubesearch", description="Sucht fünf YouTube-Treffer mit Play- und Queue-Buttons.")
    @app_commands.describe(suche="Song, Künstler oder Suchbegriff")
    async def youtubesearch(self, interaction: discord.Interaction, suche: str) -> None:
        query = " ".join(suche.split()).strip()[:200]
        if not query:
            await interaction.response.send_message("Suchbegriff fehlt.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            results = await asyncio.wait_for(asyncio.to_thread(self._search_sync, query), timeout=30)
        except Exception as exc:
            await interaction.followup.send(f"YouTube-Suche fehlgeschlagen: {exc}", ephemeral=True)
            return
        if not results:
            await interaction.followup.send("Keine YouTube-Treffer gefunden.", ephemeral=True)
            return

        lines = []
        for index, result in enumerate(results, start=1):
            lines.append(f"**{index}. [{result.title}]({result.url})**\n`{_duration_text(result.duration)}`")
        embed = discord.Embed(
            title=f"🔎 YouTube · {query}",
            description="\n\n".join(lines),
            color=YOUTUBE_COLOR,
        )
        if results[0].thumbnail:
            embed.set_thumbnail(url=results[0].thumbnail)
        embed.set_footer(text="▶ Play = Owner-only · ➕ Queue = Owner oder freigeschaltete Mods")
        await interaction.followup.send(embed=embed, view=YouTubeSearchView(self.bot, results), ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MediaInteractive(bot))
