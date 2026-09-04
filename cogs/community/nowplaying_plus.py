from __future__ import annotations

import time
from io import BytesIO
from urllib.parse import parse_qs, urlparse

import discord
from discord import app_commands
from discord.ext import commands
from PIL import Image, ImageDraw

from cogs.community.voice_suite import VoiceControls


KIND_COLORS = {
    "youtube": 0xFF2449,
    "radio": 0x2AA7FF,
    "ambient": 0x8B5CF6,
    "real ambient": 0x8B5CF6,
    "tts": 0x31C48D,
    "soundboard": 0xF59E0B,
}


def _clock(seconds: int | float | None) -> str:
    value = max(0, int(seconds or 0))
    hours, remainder = divmod(value, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def _youtube_video_id(raw: str | None) -> str | None:
    value = str(raw or "").strip()
    if not value:
        return None
    try:
        parsed = urlparse(value)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower().removeprefix("www.")
    candidate = ""
    if host == "youtu.be":
        candidate = parsed.path.strip("/").split("/")[0]
    elif host in {"youtube.com", "m.youtube.com", "music.youtube.com"}:
        if parsed.path == "/watch":
            candidate = (parse_qs(parsed.query).get("v") or [""])[0]
        elif parsed.path.startswith(("/shorts/", "/live/", "/embed/")):
            parts = parsed.path.strip("/").split("/")
            candidate = parts[1] if len(parts) > 1 else ""
    clean = "".join(ch for ch in candidate if ch.isalnum() or ch in "-_")
    return clean[:32] or None


def _progress(elapsed: int, duration: int | None, width: int = 12) -> str:
    if not duration or duration <= 0:
        return "▰" + "▱" * (width - 1)
    ratio = max(0.0, min(1.0, elapsed / duration))
    filled = max(0, min(width, round(ratio * width)))
    return "▰" * filled + "▱" * (width - filled)


def _draw_radio_cover() -> discord.File:
    size = 384
    image = Image.new("RGB", (size, size), (6, 11, 18))
    draw = ImageDraw.Draw(image)

    for radius, color in ((150, (8, 28, 43)), (122, (9, 39, 61)), (94, (10, 50, 78))):
        box = (size // 2 - radius, size // 2 - radius, size // 2 + radius, size // 2 + radius)
        draw.ellipse(box, outline=color, width=5)

    blue = (42, 167, 255)
    blue_dim = (20, 92, 140)
    panel = (12, 22, 34)
    white = (216, 239, 255)

    draw.line((94, 108, 150, 44), fill=blue, width=10)
    draw.ellipse((143, 36, 157, 50), fill=blue)
    draw.rounded_rectangle((58, 105, 326, 294), radius=28, fill=panel, outline=blue, width=7)
    draw.rounded_rectangle((86, 138, 224, 194), radius=13, fill=(4, 14, 23), outline=blue_dim, width=4)
    draw.line((104, 167, 204, 167), fill=blue, width=5)
    draw.ellipse((257, 139, 294, 176), outline=blue, width=7)
    draw.ellipse((253, 211, 300, 258), outline=blue, width=7)

    for row in range(4):
        for col in range(6):
            x = 88 + col * 24
            y = 218 + row * 16
            draw.ellipse((x, y, x + 7, y + 7), fill=blue_dim)

    draw.ellipse((271, 184, 284, 197), fill=(49, 196, 141))
    draw.line((292, 190, 310, 190), fill=white, width=4)

    buf = BytesIO()
    image.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return discord.File(buf, filename="radio-cover.png")


def _draw_generic_cover(kind: str) -> discord.File:
    size = 384
    image = Image.new("RGB", (size, size), (8, 11, 17))
    draw = ImageDraw.Draw(image)
    color = {
        "ambient": (139, 92, 246),
        "real ambient": (139, 92, 246),
        "tts": (49, 196, 141),
        "soundboard": (245, 158, 11),
    }.get(kind.lower(), (88, 101, 242))

    for x, height in enumerate((54, 92, 132, 176, 116, 76, 148, 104, 62)):
        left = 52 + x * 31
        top = size // 2 - height // 2
        draw.rounded_rectangle((left, top, left + 13, top + height), radius=6, fill=color)
    draw.ellipse((141, 141, 243, 243), outline=(225, 232, 240), width=8)
    draw.ellipse((170, 170, 214, 214), fill=color)

    buf = BytesIO()
    image.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return discord.File(buf, filename="media-cover.png")


class NowPlayingPlus(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="nowplaying", description="Zeigt die aktuelle Wiedergabe als moderne Media-Karte.")
    async def nowplaying(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None or interaction.guild is None:
            return

        voice_cog = self.bot.get_cog("VoiceSuite")
        if voice_cog is None:
            await interaction.response.send_message("Voice-System ist aktuell nicht geladen.", ephemeral=True)
            return

        state = getattr(voice_cog, "states", {}).get(interaction.guild_id)
        voice = interaction.guild.voice_client
        if state is None or voice is None or not voice.is_connected():
            embed = discord.Embed(
                title="Nothing playing",
                description="Aktuell läuft auf diesem Server keine Voice-Session.",
                color=0x202936,
            )
            embed.set_author(name="HomePi Media")
            embed.set_footer(text="Raspberry-Bot · /media nowplaying")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        elapsed = max(0, int(time.monotonic() - state.started_at))
        paused = voice.is_paused()
        playing = voice.is_playing()
        status_icon = "⏸️" if paused else "▶️" if playing else "⏹️"
        status_text = "Pausiert" if paused else "Wiedergabe läuft" if playing else "Bereit"
        kind = str(state.kind or "Media")
        kind_lower = kind.lower()
        color = KIND_COLORS.get(kind_lower, 0x5865F2)
        channel = getattr(voice, "channel", None)

        yt_cog = self.bot.get_cog("YouTubeSuite")
        yt_track = getattr(yt_cog, "current", {}).get(interaction.guild_id) if yt_cog else None
        duration = int(getattr(yt_track, "duration", 0) or 0) or None
        queue_len = len(getattr(yt_cog, "queues", {}).get(interaction.guild_id, ())) if yt_cog else 0
        loop_enabled = interaction.guild_id in getattr(yt_cog, "loop_enabled", set()) if yt_cog else False

        source_url = str(getattr(state, "source_name", "") or "").strip()
        video_id = _youtube_video_id(source_url) if kind_lower == "youtube" else None
        title = str(state.title or "Unbekannte Wiedergabe")[:256]

        embed = discord.Embed(title=title, color=color)
        embed.set_author(name="HomePi Media · Now Playing")
        if kind_lower == "youtube" and source_url.startswith(("https://", "http://")):
            embed.url = source_url

        status_line = f"{status_icon} **{status_text}**"
        if kind_lower == "radio":
            status_line += " · `LIVE`"
        embed.description = status_line

        embed.add_field(name="Quelle", value=f"`{kind}`", inline=True)
        embed.add_field(name="Lautstärke", value=f"**{state.volume}%**", inline=True)
        embed.add_field(name="Voice", value=channel.mention if channel else "—", inline=True)

        if duration:
            embed.add_field(
                name="Fortschritt",
                value=f"`{_clock(elapsed)} / {_clock(duration)}`\n{_progress(elapsed, duration)}",
                inline=False,
            )
        else:
            embed.add_field(name="Laufzeit", value=f"`{_clock(elapsed)}`", inline=True)

        if kind_lower == "youtube":
            embed.add_field(name="Loop", value="🔁 **An**" if loop_enabled else "➡️ Aus", inline=True)
            embed.add_field(name="Queue", value=f"**{queue_len}** Titel", inline=True)
        elif kind_lower == "radio":
            row = await self.bot.database.fetchone(
                "SELECT COALESCE(genre,'') genre,COALESCE(homepage,'') homepage "
                "FROM voice_radio_stations WHERE guild_id=? AND lower(name)=lower(?)",
                (interaction.guild_id, state.title),
            )
            if row and row["genre"]:
                embed.add_field(name="Genre", value=str(row["genre"])[:100], inline=True)
            homepage = str(row["homepage"] or "").strip() if row else ""
            if homepage.startswith("https://"):
                embed.add_field(name="Sender", value=f"[Website öffnen]({homepage})", inline=True)

        if state.ends_at:
            left = max(0, int(state.ends_at - time.monotonic()))
            embed.add_field(name="Sleep Timer", value=f"🌙 `{_clock(left)}`", inline=True)

        starter = "Dashboard" if not state.started_by else f"<@{state.started_by}>"
        embed.add_field(name="Gestartet von", value=starter, inline=True)

        file: discord.File | None = None
        if video_id:
            embed.set_thumbnail(url=f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg")
        elif kind_lower == "radio":
            file = _draw_radio_cover()
            embed.set_thumbnail(url="attachment://radio-cover.png")
        else:
            file = _draw_generic_cover(kind)
            embed.set_thumbnail(url="attachment://media-cover.png")

        embed.set_footer(text="Raspberry-Bot · Live Controls · /media nowplaying")

        kwargs = {
            "embed": embed,
            "view": VoiceControls(voice_cog, interaction.guild_id),
            "allowed_mentions": discord.AllowedMentions.none(),
        }
        if file is not None:
            kwargs["file"] = file
        await interaction.response.send_message(**kwargs)


def _remove_legacy_nowplaying(bot: commands.Bot) -> None:
    media = bot.tree.get_command("media")
    if isinstance(media, app_commands.Group):
        legacy = media.get_command("nowplaying")
        if legacy is not None:
            media.remove_command("nowplaying")
            legacy.parent = None
            return
    legacy = bot.tree.remove_command("nowplaying")
    if legacy is not None:
        legacy.parent = None


async def setup(bot: commands.Bot) -> None:
    _remove_legacy_nowplaying(bot)
    await bot.add_cog(NowPlayingPlus(bot))


async def teardown(bot: commands.Bot) -> None:
    if bot.tree.get_command("nowplaying") is not None:
        return
    media = bot.tree.get_command("media")
    if isinstance(media, app_commands.Group) and media.get_command("nowplaying") is not None:
        return
    voice_cog = bot.get_cog("VoiceSuite")
    if voice_cog is None:
        return
    for command in getattr(voice_cog, "__cog_app_commands__", ()):
        if getattr(command, "name", None) == "nowplaying":
            command.parent = None
            bot.tree.add_command(command)
            break
