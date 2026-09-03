from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import discord
from discord import app_commands
from discord.ext import commands, tasks

try:
    from gtts import gTTS
except ImportError:  # pragma: no cover - handled at runtime
    gTTS = None

logger = logging.getLogger(__name__)

MAX_GLOBAL_VOICE = 2
IDLE_DISCONNECT_SECONDS = 5 * 60
DEFAULT_VOLUME = 65

BUILTIN_SOUNDS: dict[str, tuple[str, str]] = {
    "airhorn": ("Airhorn", "sine=frequency=440:duration=0.8[a];sine=frequency=554:duration=0.8[b];[a][b]amix=inputs=2,volume=0.32"),
    "success": ("Success", "sine=frequency=660:duration=0.12[a];sine=frequency=880:duration=0.24[b];[a][b]concat=n=2:v=0:a=1,volume=0.35"),
    "error": ("Error", "sine=frequency=220:duration=0.18[a];sine=frequency=150:duration=0.32[b];[a][b]concat=n=2:v=0:a=1,volume=0.38"),
    "dramatic": ("Dramatic", "sine=frequency=196:duration=0.28[a];sine=frequency=147:duration=0.7[b];[a][b]concat=n=2:v=0:a=1,volume=0.32"),
    "ping": ("Ping", "sine=frequency=1047:duration=0.18,volume=0.3"),
}


@dataclass(frozen=True, slots=True)
class AmbientScene:
    label: str
    icon: str
    description: str
    graph: str


# Multi-layered procedural scenes: several filtered/noise/tonal layers instead of
# one flat white-noise source. They remain lightweight enough for the Pi 3 B+.
AMBIENT_SCENES: dict[str, AmbientScene] = {
    "rain": AmbientScene(
        "Regen am Fenster", "🌧️", "Breitbandiger Regen, feine Tropfen und tiefer Raum-Rumble.",
        "anoisesrc=color=pink:amplitude=0.10:r=48000,highpass=f=420,lowpass=f=7200[a];"
        "anoisesrc=color=white:amplitude=0.025:r=48000,highpass=f=3500,lowpass=f=10500,tremolo=f=7:d=0.28[b];"
        "anoisesrc=color=brown:amplitude=0.022:r=48000,lowpass=f=170[c];"
        "[a][b][c]amix=inputs=3:normalize=0,alimiter=limit=0.82",
    ),
    "storm": AmbientScene(
        "Gewitter", "⛈️", "Dichter Regen, tiefer Donnerteppich und langsame Druckwellen.",
        "anoisesrc=color=pink:amplitude=0.12:r=48000,highpass=f=350,lowpass=f=6500[a];"
        "anoisesrc=color=brown:amplitude=0.05:r=48000,lowpass=f=240,tremolo=f=0.09:d=0.82[b];"
        "sine=frequency=52:sample_rate=48000,volume=0.025,tremolo=f=0.07:d=0.90[c];"
        "[a][b][c]amix=inputs=3:normalize=0,alimiter=limit=0.84",
    ),
    "fireplace": AmbientScene(
        "Kamin", "🔥", "Tiefe Glut, knackendes Feuer und wechselndes Knistern.",
        "anoisesrc=color=brown:amplitude=0.04:r=48000,lowpass=f=520[a];"
        "anoisesrc=color=white:amplitude=0.032:r=48000,highpass=f=1100,lowpass=f=6500,tremolo=f=4.8:d=0.84[b];"
        "anoisesrc=color=pink:amplitude=0.018:r=48000,highpass=f=500,lowpass=f=2200,tremolo=f=0.6:d=0.55[c];"
        "[a][b][c]amix=inputs=3:normalize=0,alimiter=limit=0.78",
    ),
    "forest": AmbientScene(
        "Wald", "🌲", "Wind in Blättern mit dezenten Vogel- und Insekten-Tönen.",
        "anoisesrc=color=pink:amplitude=0.042:r=48000,highpass=f=180,lowpass=f=4200,tremolo=f=0.13:d=0.35[a];"
        "sine=frequency=2380:sample_rate=48000,volume=0.012,tremolo=f=0.37:d=0.97[b];"
        "sine=frequency=3180:sample_rate=48000,volume=0.008,tremolo=f=0.23:d=0.98[c];"
        "anoisesrc=color=brown:amplitude=0.018:r=48000,lowpass=f=260[d];"
        "[a][b][c][d]amix=inputs=4:normalize=0,alimiter=limit=0.76",
    ),
    "cafe": AmbientScene(
        "Café", "☕", "Gedämpfter Raum, tiefer Murmelteppich, Lüftung und Geschirr-Höhen.",
        "anoisesrc=color=pink:amplitude=0.052:r=48000,highpass=f=130,lowpass=f=1850[a];"
        "anoisesrc=color=brown:amplitude=0.022:r=48000,lowpass=f=320[b];"
        "anoisesrc=color=white:amplitude=0.010:r=48000,highpass=f=2500,lowpass=f=6200,tremolo=f=1.9:d=0.45[c];"
        "sine=frequency=60:sample_rate=48000,volume=0.009[d];"
        "[a][b][c][d]amix=inputs=4:normalize=0,alimiter=limit=0.74",
    ),
    "ocean": AmbientScene(
        "Ozean", "🌊", "Langsame Brandung mit Luft- und Gischt-Layer.",
        "anoisesrc=color=brown:amplitude=0.07:r=48000,lowpass=f=520,tremolo=f=0.10:d=0.75[a];"
        "anoisesrc=color=pink:amplitude=0.055:r=48000,highpass=f=480,lowpass=f=5200,tremolo=f=0.12:d=0.82[b];"
        "anoisesrc=color=white:amplitude=0.012:r=48000,highpass=f=4200,lowpass=f=9000,tremolo=f=0.16:d=0.78[c];"
        "[a][b][c]amix=inputs=3:normalize=0,alimiter=limit=0.80",
    ),
    "train": AmbientScene(
        "Nachtzug", "🚆", "Schienen-Rhythmus, Motor-Rumble und gedämpftes Fahrtgeräusch.",
        "anoisesrc=color=brown:amplitude=0.045:r=48000,lowpass=f=480[a];"
        "anoisesrc=color=pink:amplitude=0.030:r=48000,highpass=f=260,lowpass=f=2700,tremolo=f=2.15:d=0.68[b];"
        "sine=frequency=55:sample_rate=48000,volume=0.020[c];"
        "sine=frequency=92:sample_rate=48000,volume=0.010,tremolo=f=1.07:d=0.55[d];"
        "[a][b][c][d]amix=inputs=4:normalize=0,alimiter=limit=0.78",
    ),
    "night": AmbientScene(
        "Sommernacht", "🌙", "Leichter Nachtwind mit Grillen-/Insekten-ähnlichen Tonlagen.",
        "anoisesrc=color=pink:amplitude=0.022:r=48000,highpass=f=120,lowpass=f=2800,tremolo=f=0.09:d=0.40[a];"
        "sine=frequency=4180:sample_rate=48000,volume=0.010,tremolo=f=6.2:d=0.96[b];"
        "sine=frequency=5050:sample_rate=48000,volume=0.006,tremolo=f=7.7:d=0.97[c];"
        "anoisesrc=color=brown:amplitude=0.012:r=48000,lowpass=f=180[d];"
        "[a][b][c][d]amix=inputs=4:normalize=0,alimiter=limit=0.72",
    ),
    "spaceship": AmbientScene(
        "Raumschiff", "🛰️", "Tiefe Maschinen-Drones, Lüftung und moduliertes Elektronik-Hum.",
        "sine=frequency=48:sample_rate=48000,volume=0.028[a];"
        "sine=frequency=73:sample_rate=48000,volume=0.018,tremolo=f=0.08:d=0.38[b];"
        "sine=frequency=111:sample_rate=48000,volume=0.010,tremolo=f=0.17:d=0.55[c];"
        "anoisesrc=color=pink:amplitude=0.024:r=48000,highpass=f=160,lowpass=f=1800[d];"
        "[a][b][c][d]amix=inputs=4:normalize=0,alimiter=limit=0.74",
    ),
    "fan": AmbientScene(
        "Ventilator", "🌀", "Konstanter Luftstrom mit Motor-Grundton und leichtem Puls.",
        "anoisesrc=color=pink:amplitude=0.052:r=48000,highpass=f=160,lowpass=f=3500[a];"
        "sine=frequency=50:sample_rate=48000,volume=0.020[b];"
        "sine=frequency=100:sample_rate=48000,volume=0.008,tremolo=f=0.7:d=0.28[c];"
        "[a][b][c]amix=inputs=3:normalize=0,alimiter=limit=0.76",
    ),
    "city": AmbientScene(
        "Stadt bei Nacht", "🌆", "Tiefer Verkehr, diffuse Straße und entfernte Motorfrequenzen.",
        "anoisesrc=color=brown:amplitude=0.045:r=48000,lowpass=f=420[a];"
        "anoisesrc=color=pink:amplitude=0.032:r=48000,highpass=f=180,lowpass=f=2300,tremolo=f=0.19:d=0.33[b];"
        "sine=frequency=78:sample_rate=48000,volume=0.010,tremolo=f=0.11:d=0.70[c];"
        "[a][b][c]amix=inputs=3:normalize=0,alimiter=limit=0.76",
    ),
}


@dataclass(slots=True)
class PlaybackState:
    token: int
    title: str
    kind: str
    started_at: float
    started_by: int
    last_activity: float
    volume: int
    source_name: str | None = None
    temporary_file: str | None = None
    ends_at: float | None = None
    audio_source: discord.PCMVolumeTransformer | None = None


def _embed(title: str, text: str, color: int = 0x5865F2) -> discord.Embed:
    embed = discord.Embed(title=title, description=text, color=color)
    embed.set_footer(text="Raspberry Voice · max. 2 parallele Voice-Sessions")
    return embed


def _safe_remote_audio_url(raw: str) -> str:
    value = raw.strip()
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("Es sind nur öffentliche `https://` Audio-URLs erlaubt.")
    host = parsed.hostname.lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        raise ValueError("Lokale Ziele sind nicht erlaubt.")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return value
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
        raise ValueError("Private/lokale IP-Adressen sind nicht erlaubt.")
    return value


def _clean_name(raw: str) -> str:
    value = " ".join(raw.strip().split())[:48]
    if not value or not all(ch.isalnum() or ch in " -_().&+" for ch in value):
        raise ValueError("Der Name darf nur Buchstaben, Zahlen, Leerzeichen und `- _ ( ) . & +` enthalten.")
    return value


def _clamp_volume(value: int) -> int:
    return max(10, min(120, int(value)))


class VoiceControls(discord.ui.View):
    def __init__(self, cog: "VoiceSuite", guild_id: int) -> None:
        super().__init__(timeout=600)
        self.cog = cog
        self.guild_id = guild_id

    async def _voice(self, interaction: discord.Interaction) -> discord.VoiceClient | None:
        if interaction.guild_id != self.guild_id:
            await interaction.response.send_message("Diese Controls gehören zu einem anderen Server.", ephemeral=True)
            return None
        voice = interaction.guild.voice_client if interaction.guild else None
        if voice is None or not voice.is_connected():
            await interaction.response.send_message("Der Bot ist aktuell in keinem Voice-Channel.", ephemeral=True)
            return None
        return voice

    @discord.ui.button(label="-10%", emoji="🔉", style=discord.ButtonStyle.secondary)
    async def volume_down(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        voice = await self._voice(interaction)
        if voice is None:
            return
        try:
            volume = self.cog.set_session_volume(self.guild_id, -10, relative=True)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await interaction.response.send_message(f"🔉 Lautstärke: **{volume}%**", ephemeral=True)

    @discord.ui.button(label="Pause/Resume", emoji="⏯️", style=discord.ButtonStyle.primary)
    async def pause_resume(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        voice = await self._voice(interaction)
        if voice is None:
            return
        if voice.is_paused():
            voice.resume()
            state = self.cog.states.get(self.guild_id)
            if state:
                state.last_activity = time.monotonic()
            await interaction.response.send_message("▶️ Wiedergabe fortgesetzt.", ephemeral=True)
        elif voice.is_playing():
            voice.pause()
            await interaction.response.send_message("⏸️ Wiedergabe pausiert.", ephemeral=True)
        else:
            await interaction.response.send_message("Aktuell läuft nichts.", ephemeral=True)

    @discord.ui.button(label="Stop", emoji="⏹️", style=discord.ButtonStyle.secondary)
    async def stop(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        voice = await self._voice(interaction)
        if voice is None:
            return
        voice.stop()
        await interaction.response.send_message("⏹️ Wiedergabe gestoppt.", ephemeral=True)

    @discord.ui.button(label="+10%", emoji="🔊", style=discord.ButtonStyle.secondary)
    async def volume_up(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        voice = await self._voice(interaction)
        if voice is None:
            return
        try:
            volume = self.cog.set_session_volume(self.guild_id, 10, relative=True)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await interaction.response.send_message(f"🔊 Lautstärke: **{volume}%**", ephemeral=True)

    @discord.ui.button(label="Disconnect", emoji="🔌", style=discord.ButtonStyle.danger)
    async def disconnect(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        voice = await self._voice(interaction)
        if voice is None:
            return
        await voice.disconnect(force=True)
        await self.cog._clear_state(self.guild_id)
        await interaction.response.send_message("🔌 Voice-Verbindung getrennt.", ephemeral=True)


class SoundButton(discord.ui.Button):
    def __init__(self, cog: "VoiceSuite", sound_key: str, label: str, row: int) -> None:
        super().__init__(label=label[:80], style=discord.ButtonStyle.secondary, row=row)
        self.cog = cog
        self.sound_key = sound_key

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.cog.play_named_sound(interaction, self.sound_key)


class SoundboardView(discord.ui.View):
    def __init__(self, cog: "VoiceSuite", entries: list[tuple[str, str]]) -> None:
        super().__init__(timeout=600)
        for index, (key, label) in enumerate(entries[:20]):
            self.add_item(SoundButton(cog, key, label, index // 5))


class VoiceSuite(commands.Cog):
    soundboard = app_commands.Group(name="soundboard", description="Sounds und Meme-Audio im Voice-Channel")
    radio = app_commands.Group(name="radio", description="Internet-Radio mit Server-Sendern und Favoriten")
    ambientsource = app_commands.Group(name="ambientsource", description="Eigene echte Ambient-Audioquellen verwalten")
    voice = app_commands.Group(name="voice", description="Voice-Session steuern")

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.states: dict[int, PlaybackState] = {}
        self.idle_guard.start()

    async def cog_load(self) -> None:
        for sql in (
            """CREATE TABLE IF NOT EXISTS voice_soundboard (guild_id INTEGER NOT NULL,name TEXT NOT NULL,audio_url TEXT NOT NULL,created_by INTEGER NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(guild_id,name))""",
            """CREATE TABLE IF NOT EXISTS voice_radio_stations (guild_id INTEGER NOT NULL,name TEXT NOT NULL,stream_url TEXT NOT NULL,genre TEXT,homepage TEXT,created_by INTEGER NOT NULL,enabled INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(guild_id,name))""",
            """CREATE TABLE IF NOT EXISTS voice_radio_favorites (guild_id INTEGER NOT NULL,user_id INTEGER NOT NULL,station_name TEXT NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(guild_id,user_id,station_name))""",
            """CREATE TABLE IF NOT EXISTS voice_ambient_sources (guild_id INTEGER NOT NULL,name TEXT NOT NULL,audio_url TEXT NOT NULL,category TEXT,created_by INTEGER NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(guild_id,name))""",
            """CREATE TABLE IF NOT EXISTS voice_playback_history (id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER NOT NULL,kind TEXT NOT NULL,title TEXT NOT NULL,source_name TEXT,started_by INTEGER NOT NULL,started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
            "CREATE INDEX IF NOT EXISTS idx_voice_history_guild_time ON voice_playback_history(guild_id,started_at)",
        ):
            await self.bot.database.execute(sql)

    async def cog_unload(self) -> None:
        self.idle_guard.cancel()
        for voice in list(self.bot.voice_clients):
            try:
                await voice.disconnect(force=True)
            except Exception:
                pass
        for guild_id in list(self.states):
            await self._clear_state(guild_id)

    def _ffmpeg_ready(self) -> bool:
        return shutil.which("ffmpeg") is not None

    async def _get_voice(self, interaction: discord.Interaction) -> discord.VoiceClient | None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Dieser Command funktioniert nur auf einem Server.", ephemeral=True)
            return None
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message("Du musst zuerst einem Voice-Channel beitreten.", ephemeral=True)
            return None
        try:
            return await self._connect_channel(interaction.guild, interaction.user.voice.channel)
        except (ValueError, discord.ClientException, discord.HTTPException, asyncio.TimeoutError) as exc:
            logger.warning("Voice connect failed: %s", exc)
            await interaction.response.send_message("Voice-Verbindung fehlgeschlagen. Prüfe **davey/PyNaCl**, FFmpeg und die Discord-Voice-Rechte.", ephemeral=True)
            return None

    async def _connect_channel(self, guild: discord.Guild, channel: discord.VoiceChannel | discord.StageChannel) -> discord.VoiceClient:
        current = guild.voice_client
        if current and current.is_connected():
            if current.channel != channel:
                await current.move_to(channel)
            return current
        connected = sum(1 for voice in self.bot.voice_clients if voice.is_connected())
        if connected >= MAX_GLOBAL_VOICE:
            raise ValueError(f"Globales Voice-Limit von {MAX_GLOBAL_VOICE} Sessions erreicht.")
        return await channel.connect(timeout=15, reconnect=True)

    async def _clear_state(self, guild_id: int, token: int | None = None) -> None:
        state = self.states.get(guild_id)
        if state is None or (token is not None and state.token != token):
            return
        self.states.pop(guild_id, None)
        if state.temporary_file:
            try:
                Path(state.temporary_file).unlink(missing_ok=True)
            except OSError:
                pass

    def _after_playback(self, guild_id: int, token: int, error: Exception | None) -> None:
        if error:
            logger.warning("Voice playback error in guild %s: %s", guild_id, error)
        try:
            loop = self.bot.loop
            loop.call_soon_threadsafe(lambda: asyncio.create_task(self._clear_state(guild_id, token)))
        except RuntimeError:
            pass

    async def _record_history(self, guild_id: int, kind: str, title: str, source_name: str | None, started_by: int) -> None:
        try:
            await self.bot.database.execute("INSERT INTO voice_playback_history(guild_id,kind,title,source_name,started_by) VALUES(?,?,?,?,?)", (guild_id, kind, title[:180], source_name[:80] if source_name else None, started_by))
            await self.bot.database.execute("DELETE FROM voice_playback_history WHERE id IN (SELECT id FROM voice_playback_history WHERE guild_id=? ORDER BY id DESC LIMIT -1 OFFSET 250)", (guild_id,))
        except Exception:
            logger.exception("Failed to persist voice history")

    async def _start_on_voice(self, voice: discord.VoiceClient, source: discord.AudioSource, *, guild_id: int, title: str, kind: str, started_by: int, source_name: str | None = None, volume: int = DEFAULT_VOLUME, temporary_file: str | None = None, duration_minutes: int = 0) -> bool:
        if voice.is_playing() or voice.is_paused():
            voice.stop()
            await asyncio.sleep(0)
        await self._clear_state(guild_id)
        token = time.monotonic_ns()
        clean_volume = _clamp_volume(volume)
        wrapped = discord.PCMVolumeTransformer(source, volume=clean_volume / 100)
        now = time.monotonic()
        state = PlaybackState(token=token, title=title, kind=kind, started_at=now, started_by=started_by, last_activity=now, volume=clean_volume, source_name=source_name, temporary_file=temporary_file, ends_at=now + duration_minutes * 60 if duration_minutes > 0 else None, audio_source=wrapped)
        self.states[guild_id] = state
        try:
            voice.play(wrapped, after=lambda error: self._after_playback(guild_id, token, error))
        except Exception:
            await self._clear_state(guild_id, token)
            raise
        asyncio.create_task(self._record_history(guild_id, kind, title, source_name, started_by))
        return True

    async def _start_source(self, interaction: discord.Interaction, source: discord.AudioSource, *, title: str, kind: str, source_name: str | None = None, volume: int = DEFAULT_VOLUME, temporary_file: str | None = None, duration_minutes: int = 0) -> bool:
        voice = await self._get_voice(interaction)
        if voice is None:
            if temporary_file:
                Path(temporary_file).unlink(missing_ok=True)
            return False
        return await self._start_on_voice(voice, source, guild_id=int(interaction.guild_id or 0), title=title, kind=kind, started_by=interaction.user.id, source_name=source_name, volume=volume, temporary_file=temporary_file, duration_minutes=duration_minutes)

    def _lavfi_source(self, graph: str) -> discord.FFmpegPCMAudio:
        return discord.FFmpegPCMAudio(graph, before_options="-f lavfi", options="-vn -loglevel error")

    def _remote_source(self, url: str) -> discord.FFmpegPCMAudio:
        return discord.FFmpegPCMAudio(url, before_options="-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -rw_timeout 15000000", options="-vn -loglevel error")

    def set_session_volume(self, guild_id: int, value: int, *, relative: bool = False) -> int:
        state = self.states.get(guild_id)
        if state is None or state.audio_source is None:
            raise ValueError("Keine aktive Voice-Wiedergabe.")
        target = state.volume + value if relative else value
        state.volume = _clamp_volume(target)
        state.audio_source.volume = state.volume / 100
        state.last_activity = time.monotonic()
        return state.volume

    async def play_named_sound(self, interaction: discord.Interaction, sound_name: str) -> None:
        if not self._ffmpeg_ready():
            await interaction.response.send_message("FFmpeg ist auf dem Pi nicht installiert.", ephemeral=True)
            return
        key = sound_name.strip().lower()
        builtin = BUILTIN_SOUNDS.get(key)
        if builtin:
            label, graph = builtin
            ok = await self._start_source(interaction, self._lavfi_source(graph), title=label, kind="Soundboard", source_name=key, volume=75)
            if ok:
                if interaction.response.is_done():
                    await interaction.followup.send(f"🔊 **{label}**", ephemeral=True)
                else:
                    await interaction.response.send_message(f"🔊 **{label}**", ephemeral=True)
            return
        if interaction.guild_id is None:
            return
        row = await self.bot.database.fetchone("SELECT audio_url FROM voice_soundboard WHERE guild_id=? AND lower(name)=lower(?)", (interaction.guild_id, key))
        if not row:
            await interaction.response.send_message("Sound nicht gefunden. Nutze `/soundboard list`.", ephemeral=True)
            return
        ok = await self._start_source(interaction, self._remote_source(str(row["audio_url"])), title=key, kind="Soundboard", source_name=key, volume=75)
        if ok:
            if interaction.response.is_done():
                await interaction.followup.send(f"🔊 **{key}**", ephemeral=True)
            else:
                await interaction.response.send_message(f"🔊 **{key}**", ephemeral=True)

    @soundboard.command(name="play", description="Spielt einen gespeicherten oder eingebauten Sound ab.")
    async def soundboard_play(self, interaction: discord.Interaction, name: str) -> None:
        await self.play_named_sound(interaction, name)

    @soundboard.command(name="panel", description="Öffnet ein Soundboard mit Buttons.")
    async def soundboard_panel(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            return
        rows = await self.bot.database.fetchall("SELECT name FROM voice_soundboard WHERE guild_id=? ORDER BY name COLLATE NOCASE LIMIT 15", (interaction.guild_id,))
        entries = [(key, label) for key, (label, _) in BUILTIN_SOUNDS.items()]
        entries.extend((str(row["name"]), str(row["name"])) for row in rows)
        await interaction.response.send_message(embed=_embed("🔊 Soundboard", "Drücke einen Button. Der Bot tritt deinem aktuellen Voice-Channel bei."), view=SoundboardView(self, entries))

    @soundboard.command(name="add", description="Fügt eine eigene HTTPS-Audiodatei zum Soundboard hinzu.")
    @app_commands.default_permissions(manage_guild=True)
    async def soundboard_add(self, interaction: discord.Interaction, name: str, url: str) -> None:
        if interaction.guild_id is None:
            return
        try:
            clean_name = _clean_name(name).lower()
            clean_url = _safe_remote_audio_url(url)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await self.bot.database.execute("INSERT INTO voice_soundboard(guild_id,name,audio_url,created_by) VALUES(?,?,?,?) ON CONFLICT(guild_id,name) DO UPDATE SET audio_url=excluded.audio_url,created_by=excluded.created_by,created_at=CURRENT_TIMESTAMP", (interaction.guild_id, clean_name, clean_url, interaction.user.id))
        await interaction.response.send_message(f"✅ Sound `{clean_name}` gespeichert.", ephemeral=True)

    @soundboard.command(name="remove", description="Entfernt einen eigenen Soundboard-Eintrag.")
    @app_commands.default_permissions(manage_guild=True)
    async def soundboard_remove(self, interaction: discord.Interaction, name: str) -> None:
        if interaction.guild_id is None:
            return
        await self.bot.database.execute("DELETE FROM voice_soundboard WHERE guild_id=? AND lower(name)=lower(?)", (interaction.guild_id, name.strip()))
        await interaction.response.send_message("Soundboard-Eintrag entfernt.", ephemeral=True)

    @soundboard.command(name="list", description="Zeigt alle verfügbaren Sounds.")
    async def soundboard_list(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            return
        rows = await self.bot.database.fetchall("SELECT name FROM voice_soundboard WHERE guild_id=? ORDER BY name COLLATE NOCASE LIMIT 50", (interaction.guild_id,))
        builtin = ", ".join(f"`{name}`" for name in BUILTIN_SOUNDS)
        custom = ", ".join(f"`{row['name']}`" for row in rows) or "—"
        await interaction.response.send_message(embed=_embed("🔊 Sounds", f"**Built-in**\n{builtin}\n\n**Eigene Sounds**\n{custom}"), ephemeral=True)

    @app_commands.command(name="ambient", description="Spielt eine mehrschichtige Ambient-Szene im Voice-Channel.")
    @app_commands.choices(szene=[app_commands.Choice(name=f"{scene.icon} {scene.label}", value=key) for key, scene in AMBIENT_SCENES.items()])
    async def ambient(self, interaction: discord.Interaction, szene: app_commands.Choice[str], lautstaerke: app_commands.Range[int, 10, 120] = DEFAULT_VOLUME, minuten: app_commands.Range[int, 0, 480] = 0) -> None:
        if not self._ffmpeg_ready():
            await interaction.response.send_message("FFmpeg ist auf dem Pi nicht installiert.", ephemeral=True)
            return
        scene = AMBIENT_SCENES[szene.value]
        ok = await self._start_source(interaction, self._lavfi_source(scene.graph), title=scene.label, kind="Ambient", source_name=szene.value, volume=int(lautstaerke), duration_minutes=int(minuten))
        if ok:
            timer = f"\nSleep-Timer: **{int(minuten)} Min.**" if int(minuten) else ""
            await interaction.response.send_message(embed=_embed(f"{scene.icon} Ambient · {scene.label}", f"{scene.description}\nLautstärke: **{int(lautstaerke)}%**{timer}\n\n`/nowplaying` öffnet Lautstärke/Pause/Stop/Disconnect."), ephemeral=True)

    @app_commands.command(name="ambientcatalog", description="Zeigt alle verbesserten eingebauten Ambient-Szenen.")
    async def ambientcatalog(self, interaction: discord.Interaction) -> None:
        text = "\n".join(f"{scene.icon} **{scene.label}** (`{key}`)\n└ {scene.description}" for key, scene in AMBIENT_SCENES.items())
        await interaction.response.send_message(embed=_embed("🌌 Ambient-Katalog", text[:4000]), ephemeral=True)

    @ambientsource.command(name="add", description="Speichert eine echte HTTPS-Ambient-Audioquelle.")
    @app_commands.default_permissions(manage_guild=True)
    async def ambient_source_add(self, interaction: discord.Interaction, name: str, url: str, kategorie: str = "Custom") -> None:
        if interaction.guild_id is None:
            return
        try:
            clean_name = _clean_name(name)
            clean_url = _safe_remote_audio_url(url)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await self.bot.database.execute("INSERT INTO voice_ambient_sources(guild_id,name,audio_url,category,created_by) VALUES(?,?,?,?,?) ON CONFLICT(guild_id,name) DO UPDATE SET audio_url=excluded.audio_url,category=excluded.category,created_by=excluded.created_by,updated_at=CURRENT_TIMESTAMP", (interaction.guild_id, clean_name, clean_url, kategorie.strip()[:40], interaction.user.id))
        await interaction.response.send_message(f"✅ Reale Ambient-Quelle **{clean_name}** gespeichert.", ephemeral=True)

    @ambientsource.command(name="remove", description="Entfernt eine eigene Ambient-Quelle.")
    @app_commands.default_permissions(manage_guild=True)
    async def ambient_source_remove(self, interaction: discord.Interaction, name: str) -> None:
        if interaction.guild_id is None:
            return
        await self.bot.database.execute("DELETE FROM voice_ambient_sources WHERE guild_id=? AND lower(name)=lower(?)", (interaction.guild_id, name.strip()))
        await interaction.response.send_message("Ambient-Quelle entfernt.", ephemeral=True)

    @ambientsource.command(name="list", description="Zeigt echte gespeicherte Ambient-Audioquellen.")
    async def ambient_source_list(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            return
        rows = await self.bot.database.fetchall("SELECT name,COALESCE(category,'Custom') category FROM voice_ambient_sources WHERE guild_id=? ORDER BY category,name COLLATE NOCASE LIMIT 50", (interaction.guild_id,))
        text = "\n".join(f"• **{row['name']}** · {row['category']}" for row in rows) or "Noch keine Quellen."
        await interaction.response.send_message(embed=_embed("🎧 Echte Ambient-Quellen", text), ephemeral=True)

    @ambientsource.command(name="play", description="Spielt eine gespeicherte echte Ambient-Aufnahme/Stream.")
    async def ambient_source_play(self, interaction: discord.Interaction, name: str, lautstaerke: app_commands.Range[int, 10, 120] = DEFAULT_VOLUME, minuten: app_commands.Range[int, 0, 480] = 0) -> None:
        if interaction.guild_id is None:
            return
        row = await self.bot.database.fetchone("SELECT name,audio_url,category FROM voice_ambient_sources WHERE guild_id=? AND lower(name)=lower(?)", (interaction.guild_id, name.strip()))
        if not row:
            await interaction.response.send_message("Ambient-Quelle nicht gefunden.", ephemeral=True)
            return
        if not self._ffmpeg_ready():
            await interaction.response.send_message("FFmpeg ist auf dem Pi nicht installiert.", ephemeral=True)
            return
        ok = await self._start_source(interaction, self._remote_source(str(row["audio_url"])), title=str(row["name"]), kind="Real Ambient", source_name=str(row["name"]), volume=int(lautstaerke), duration_minutes=int(minuten))
        if ok:
            await interaction.response.send_message(embed=_embed("🎧 Real Ambient", f"**{row['name']}** · {row['category'] or 'Custom'}\nLautstärke: **{int(lautstaerke)}%**"), ephemeral=True)

    @ambient_source_play.autocomplete("name")
    async def ambient_source_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        if interaction.guild_id is None:
            return []
        rows = await self.bot.database.fetchall("SELECT name FROM voice_ambient_sources WHERE guild_id=? AND lower(name) LIKE lower(?) ORDER BY name COLLATE NOCASE LIMIT 20", (interaction.guild_id, f"%{current.strip()}%"))
        return [app_commands.Choice(name=str(row["name"]), value=str(row["name"])) for row in rows]

    @radio.command(name="add", description="Speichert einen Internet-Radiosender per HTTPS-Stream-URL.")
    @app_commands.default_permissions(manage_guild=True)
    async def radio_add(self, interaction: discord.Interaction, name: str, stream_url: str, genre: str = "", homepage: str = "") -> None:
        if interaction.guild_id is None:
            return
        try:
            clean_name = _clean_name(name)
            clean_stream = _safe_remote_audio_url(stream_url)
            clean_home = _safe_remote_audio_url(homepage) if homepage.strip() else ""
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await self.bot.database.execute("INSERT INTO voice_radio_stations(guild_id,name,stream_url,genre,homepage,created_by) VALUES(?,?,?,?,?,?) ON CONFLICT(guild_id,name) DO UPDATE SET stream_url=excluded.stream_url,genre=excluded.genre,homepage=excluded.homepage,created_by=excluded.created_by,enabled=1,updated_at=CURRENT_TIMESTAMP", (interaction.guild_id, clean_name, clean_stream, genre.strip()[:60], clean_home, interaction.user.id))
        await interaction.response.send_message(f"📻 Sender **{clean_name}** gespeichert.", ephemeral=True)

    @radio.command(name="remove", description="Entfernt einen gespeicherten Radiosender.")
    @app_commands.default_permissions(manage_guild=True)
    async def radio_remove(self, interaction: discord.Interaction, name: str) -> None:
        if interaction.guild_id is None:
            return
        await self.bot.database.execute("DELETE FROM voice_radio_favorites WHERE guild_id=? AND lower(station_name)=lower(?)", (interaction.guild_id, name.strip()))
        await self.bot.database.execute("DELETE FROM voice_radio_stations WHERE guild_id=? AND lower(name)=lower(?)", (interaction.guild_id, name.strip()))
        await interaction.response.send_message("Radiosender entfernt.", ephemeral=True)

    @radio.command(name="play", description="Spielt einen gespeicherten Internet-Radiosender.")
    async def radio_play(self, interaction: discord.Interaction, station: str, lautstaerke: app_commands.Range[int, 10, 120] = DEFAULT_VOLUME) -> None:
        if interaction.guild_id is None:
            return
        if not self._ffmpeg_ready():
            await interaction.response.send_message("FFmpeg ist auf dem Pi nicht installiert.", ephemeral=True)
            return
        row = await self.bot.database.fetchone("SELECT name,stream_url,genre FROM voice_radio_stations WHERE guild_id=? AND enabled=1 AND lower(name)=lower(?)", (interaction.guild_id, station.strip()))
        if not row:
            await interaction.response.send_message("Sender nicht gefunden. Nutze `/radio list`.", ephemeral=True)
            return
        ok = await self._start_source(interaction, self._remote_source(str(row["stream_url"])), title=str(row["name"]), kind="Radio", source_name=str(row["name"]), volume=int(lautstaerke))
        if ok:
            await interaction.response.send_message(embed=_embed("📻 Radio", f"**{row['name']}**\nGenre: {row['genre'] or '—'} · Lautstärke: **{int(lautstaerke)}%**\n`/nowplaying` öffnet die Live-Controls.", 0xE91E63), ephemeral=True)

    @radio_play.autocomplete("station")
    async def radio_station_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        if interaction.guild_id is None:
            return []
        rows = await self.bot.database.fetchall("SELECT name,COALESCE(genre,'') genre FROM voice_radio_stations WHERE guild_id=? AND enabled=1 AND lower(name) LIKE lower(?) ORDER BY name COLLATE NOCASE LIMIT 20", (interaction.guild_id, f"%{current.strip()}%"))
        return [app_commands.Choice(name=(f"{row['name']} · {row['genre']}" if row["genre"] else str(row["name"]))[:100], value=str(row["name"])) for row in rows]

    @radio.command(name="list", description="Zeigt gespeicherte Radiosender.")
    async def radio_list(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            return
        rows = await self.bot.database.fetchall("SELECT name,COALESCE(genre,'') genre FROM voice_radio_stations WHERE guild_id=? AND enabled=1 ORDER BY name COLLATE NOCASE LIMIT 50", (interaction.guild_id,))
        text = "\n".join(f"📻 **{row['name']}**{f' · {row[\"genre\"]}' if row['genre'] else ''}" for row in rows) or "Noch keine Sender. Admins können `/radio add` verwenden."
        await interaction.response.send_message(embed=_embed("📻 Sender", text, 0xE91E63), ephemeral=True)

    @radio.command(name="favorite", description="Fügt einen Sender zu deinen Favoriten hinzu.")
    async def radio_favorite(self, interaction: discord.Interaction, station: str) -> None:
        if interaction.guild_id is None:
            return
        row = await self.bot.database.fetchone("SELECT name FROM voice_radio_stations WHERE guild_id=? AND lower(name)=lower(?)", (interaction.guild_id, station.strip()))
        if not row:
            await interaction.response.send_message("Sender nicht gefunden.", ephemeral=True)
            return
        await self.bot.database.execute("INSERT OR IGNORE INTO voice_radio_favorites(guild_id,user_id,station_name) VALUES(?,?,?)", (interaction.guild_id, interaction.user.id, str(row["name"])))
        await interaction.response.send_message(f"⭐ **{row['name']}** ist jetzt Favorit.", ephemeral=True)

    @radio.command(name="unfavorite", description="Entfernt einen Sender aus deinen Favoriten.")
    async def radio_unfavorite(self, interaction: discord.Interaction, station: str) -> None:
        if interaction.guild_id is None:
            return
        await self.bot.database.execute("DELETE FROM voice_radio_favorites WHERE guild_id=? AND user_id=? AND lower(station_name)=lower(?)", (interaction.guild_id, interaction.user.id, station.strip()))
        await interaction.response.send_message("Favorit entfernt.", ephemeral=True)

    @radio.command(name="favorites", description="Zeigt deine gespeicherten Radiosender-Favoriten.")
    async def radio_favorites(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            return
        rows = await self.bot.database.fetchall("SELECT f.station_name,COALESCE(s.genre,'') genre FROM voice_radio_favorites f LEFT JOIN voice_radio_stations s ON s.guild_id=f.guild_id AND lower(s.name)=lower(f.station_name) WHERE f.guild_id=? AND f.user_id=? ORDER BY f.station_name COLLATE NOCASE LIMIT 50", (interaction.guild_id, interaction.user.id))
        text = "\n".join(f"⭐ **{row['station_name']}**{f' · {row[\"genre\"]}' if row['genre'] else ''}" for row in rows) or "Noch keine Favoriten."
        await interaction.response.send_message(embed=_embed("⭐ Radio-Favoriten", text, 0xF1C40F), ephemeral=True)

    @radio.command(name="history", description="Zeigt die letzten Radio-/Ambient-Wiedergaben.")
    async def radio_history(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            return
        rows = await self.bot.database.fetchall("SELECT kind,title,source_name,started_by,started_at FROM voice_playback_history WHERE guild_id=? AND kind IN ('Radio','Ambient','Real Ambient') ORDER BY id DESC LIMIT 15", (interaction.guild_id,))
        text = "\n".join(f"• **{row['title']}** · `{row['kind']}` · <@{row['started_by']}> · {row['started_at']}" for row in rows) or "Noch keine Wiedergaben."
        await interaction.response.send_message(embed=_embed("🕘 Media History", text, 0x95A5A6), ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    @app_commands.command(name="tts", description="Spricht Text im aktuellen Voice-Channel.")
    @app_commands.describe(text="Maximal 400 Zeichen", sprache="z. B. de, en, fr")
    async def tts(self, interaction: discord.Interaction, text: str, sprache: str = "de") -> None:
        if not self._ffmpeg_ready():
            await interaction.response.send_message("FFmpeg ist auf dem Pi nicht installiert.", ephemeral=True)
            return
        if gTTS is None:
            await interaction.response.send_message("Das Python-Paket `gTTS` fehlt.", ephemeral=True)
            return
        clean = " ".join(text.split())[:400]
        if not clean:
            await interaction.response.send_message("Text darf nicht leer sein.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        fd, temp_path = tempfile.mkstemp(prefix="raspberry-tts-", suffix=".mp3")
        os.close(fd)
        try:
            await asyncio.to_thread(gTTS(text=clean, lang=sprache.strip().lower()[:8]).save, temp_path)
            ok = await self._start_source(interaction, discord.FFmpegPCMAudio(temp_path, options="-vn -loglevel error"), title=clean[:80], kind="TTS", source_name=sprache.strip().lower()[:8], volume=85, temporary_file=temp_path)
        except Exception as exc:
            Path(temp_path).unlink(missing_ok=True)
            logger.warning("TTS failed: %s", exc)
            await interaction.followup.send("TTS konnte nicht erzeugt werden. Prüfe Sprache und Internetverbindung.", ephemeral=True)
            return
        if ok:
            await interaction.followup.send("🗣️ TTS wird abgespielt. Der Text wird über den externen gTTS-Dienst erzeugt.", ephemeral=True)

    @app_commands.command(name="nowplaying", description="Zeigt aktuelle Voice-Wiedergabe mit Lautstärke-Controls.")
    async def nowplaying(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            return
        state = self.states.get(interaction.guild_id)
        voice = interaction.guild.voice_client if interaction.guild else None
        if state is None or voice is None or not voice.is_connected():
            await interaction.response.send_message("Aktuell läuft keine Voice-Session.", ephemeral=True)
            return
        elapsed = max(0, int(time.monotonic() - state.started_at))
        status = "pausiert" if voice.is_paused() else "läuft" if voice.is_playing() else "bereit"
        timer = ""
        if state.ends_at:
            left = max(0, int(state.ends_at - time.monotonic()))
            timer = f"\nSleep-Timer: **{left // 60}:{left % 60:02d}**"
        await interaction.response.send_message(embed=_embed("🎧 Now Playing", f"**{state.title}**\nTyp: `{state.kind}` · Status: **{status}**\nLaufzeit: **{elapsed // 60}:{elapsed % 60:02d}** · Lautstärke: **{state.volume}%**{timer}\nGestartet von <@{state.started_by}>"), view=VoiceControls(self, interaction.guild_id), allowed_mentions=discord.AllowedMentions.none())

    @voice.command(name="status", description="Zeigt Voice-Limit, Session und FFmpeg-Status.")
    async def voice_status(self, interaction: discord.Interaction) -> None:
        active = sum(1 for v in self.bot.voice_clients if v.is_connected())
        state = self.states.get(interaction.guild_id or 0)
        current = f"**{state.title}** · `{state.kind}` · {state.volume}%" if state else "Keine aktive Session auf diesem Server."
        await interaction.response.send_message(embed=_embed("🎙️ Voice Status", f"Globale Sessions: **{active}/{MAX_GLOBAL_VOICE}**\nFFmpeg: **{'bereit' if self._ffmpeg_ready() else 'fehlt'}**\n\n{current}"), ephemeral=True)

    @voice.command(name="volume", description="Ändert die Lautstärke der aktuellen Wiedergabe.")
    async def voice_volume(self, interaction: discord.Interaction, prozent: app_commands.Range[int, 10, 120]) -> None:
        try:
            value = self.set_session_volume(interaction.guild_id or 0, int(prozent))
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await interaction.response.send_message(f"🔊 Lautstärke auf **{value}%** gesetzt.", ephemeral=True)

    @voice.command(name="sleep", description="Setzt einen Sleep-Timer für die aktuelle Voice-Session.")
    async def voice_sleep(self, interaction: discord.Interaction, minuten: app_commands.Range[int, 1, 480]) -> None:
        state = self.states.get(interaction.guild_id or 0)
        if not state:
            await interaction.response.send_message("Keine aktive Voice-Wiedergabe.", ephemeral=True)
            return
        state.ends_at = time.monotonic() + int(minuten) * 60
        await interaction.response.send_message(f"🌙 Sleep-Timer auf **{int(minuten)} Minuten** gesetzt.", ephemeral=True)

    @voice.command(name="leave", description="Trennt den Bot vom Voice-Channel.")
    async def voice_leave(self, interaction: discord.Interaction) -> None:
        voice = interaction.guild.voice_client if interaction.guild else None
        if not voice or not voice.is_connected():
            await interaction.response.send_message("Bot ist nicht im Voice-Channel.", ephemeral=True)
            return
        await voice.disconnect(force=True)
        await self._clear_state(interaction.guild_id or 0)
        await interaction.response.send_message("🔌 Voice getrennt.", ephemeral=True)

    async def dashboard_play_radio(self, guild_id: int, channel_id: int, station_name: str, volume: int = DEFAULT_VOLUME) -> str:
        if not self._ffmpeg_ready():
            raise RuntimeError("FFmpeg ist nicht installiert.")
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise ValueError("Guild nicht gefunden.")
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
            raise ValueError("Voice-Channel nicht gefunden.")
        row = await self.bot.database.fetchone("SELECT name,stream_url FROM voice_radio_stations WHERE guild_id=? AND enabled=1 AND lower(name)=lower(?)", (guild_id, station_name.strip()))
        if not row:
            raise ValueError("Radiosender nicht gefunden.")
        voice = await self._connect_channel(guild, channel)
        await self._start_on_voice(voice, self._remote_source(str(row["stream_url"])), guild_id=guild_id, title=str(row["name"]), kind="Radio", started_by=0, source_name=str(row["name"]), volume=volume)
        return f"Radio {row['name']} in {channel.name} gestartet"

    async def dashboard_play_ambient(self, guild_id: int, channel_id: int, scene_key: str, volume: int = DEFAULT_VOLUME, minutes: int = 0) -> str:
        if not self._ffmpeg_ready():
            raise RuntimeError("FFmpeg ist nicht installiert.")
        scene = AMBIENT_SCENES.get(scene_key)
        if scene is None:
            raise ValueError("Ambient-Szene nicht gefunden.")
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise ValueError("Guild nicht gefunden.")
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
            raise ValueError("Voice-Channel nicht gefunden.")
        voice = await self._connect_channel(guild, channel)
        await self._start_on_voice(voice, self._lavfi_source(scene.graph), guild_id=guild_id, title=scene.label, kind="Ambient", started_by=0, source_name=scene_key, volume=volume, duration_minutes=max(0, min(480, int(minutes))))
        return f"Ambient {scene.label} in {channel.name} gestartet"

    async def dashboard_play_ambient_source(self, guild_id: int, channel_id: int, source_name: str, volume: int = DEFAULT_VOLUME, minutes: int = 0) -> str:
        if not self._ffmpeg_ready():
            raise RuntimeError("FFmpeg ist nicht installiert.")
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise ValueError("Guild nicht gefunden.")
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
            raise ValueError("Voice-Channel nicht gefunden.")
        row = await self.bot.database.fetchone("SELECT name,audio_url FROM voice_ambient_sources WHERE guild_id=? AND lower(name)=lower(?)", (guild_id, source_name.strip()))
        if not row:
            raise ValueError("Ambient-Quelle nicht gefunden.")
        voice = await self._connect_channel(guild, channel)
        await self._start_on_voice(voice, self._remote_source(str(row["audio_url"])), guild_id=guild_id, title=str(row["name"]), kind="Real Ambient", started_by=0, source_name=str(row["name"]), volume=volume, duration_minutes=max(0, min(480, int(minutes))))
        return f"Ambient {row['name']} in {channel.name} gestartet"

    async def dashboard_stop(self, guild_id: int) -> str:
        guild = self.bot.get_guild(guild_id)
        voice = guild.voice_client if guild else None
        if not voice or not voice.is_connected():
            raise ValueError("Keine aktive Voice-Verbindung.")
        voice.stop()
        return "Wiedergabe gestoppt"

    async def dashboard_disconnect(self, guild_id: int) -> str:
        guild = self.bot.get_guild(guild_id)
        voice = guild.voice_client if guild else None
        if not voice or not voice.is_connected():
            raise ValueError("Keine aktive Voice-Verbindung.")
        await voice.disconnect(force=True)
        await self._clear_state(guild_id)
        return "Voice-Verbindung getrennt"

    @tasks.loop(seconds=30)
    async def idle_guard(self) -> None:
        now = time.monotonic()
        for voice in list(self.bot.voice_clients):
            if not voice.guild:
                continue
            gid = voice.guild.id
            state = self.states.get(gid)
            if state and state.ends_at and now >= state.ends_at:
                try:
                    voice.stop()
                    await voice.disconnect(force=True)
                finally:
                    await self._clear_state(gid, state.token)
                continue
            if voice.is_playing() or voice.is_paused():
                if state:
                    state.last_activity = now
                continue
            last = state.last_activity if state else now
            if now - last >= IDLE_DISCONNECT_SECONDS:
                try:
                    await voice.disconnect(force=True)
                finally:
                    await self._clear_state(gid)

    @idle_guard.before_loop
    async def before_idle_guard(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(VoiceSuite(bot))
