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

BUILTIN_SOUNDS: dict[str, tuple[str, str]] = {
    "airhorn": ("Airhorn", "sine=frequency=440:duration=0.8[a];sine=frequency=554:duration=0.8[b];[a][b]amix=inputs=2,volume=0.32"),
    "success": ("Success", "sine=frequency=660:duration=0.12[a];sine=frequency=880:duration=0.24[b];[a][b]concat=n=2:v=0:a=1,volume=0.35"),
    "error": ("Error", "sine=frequency=220:duration=0.18[a];sine=frequency=150:duration=0.32[b];[a][b]concat=n=2:v=0:a=1,volume=0.38"),
    "dramatic": ("Dramatic", "sine=frequency=196:duration=0.28[a];sine=frequency=147:duration=0.7[b];[a][b]concat=n=2:v=0:a=1,volume=0.32"),
    "ping": ("Ping", "sine=frequency=1047:duration=0.18,volume=0.3"),
}

AMBIENT_FILTERS: dict[str, tuple[str, str]] = {
    "rain": ("Regen", "anoisesrc=color=pink:amplitude=0.18,highpass=f=500,lowpass=f=6500,volume=0.42"),
    "storm": ("Gewitter", "anoisesrc=color=brown:amplitude=0.24,lowpass=f=1400,volume=0.55"),
    "fireplace": ("Kamin", "anoisesrc=color=white:amplitude=0.10,highpass=f=900,lowpass=f=4300,volume=0.34"),
    "forest": ("Wald", "anoisesrc=color=pink:amplitude=0.12,highpass=f=220,lowpass=f=5200,volume=0.28"),
    "cafe": ("Café", "anoisesrc=color=brown:amplitude=0.10,highpass=f=160,lowpass=f=2800,volume=0.25"),
}


@dataclass(slots=True)
class PlaybackState:
    title: str
    kind: str
    started_at: float
    started_by: int
    last_activity: float
    temporary_file: str | None = None


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
    if host in {"localhost", "localhost.localdomain"}:
        raise ValueError("Lokale Ziele sind nicht erlaubt.")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return value
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
        raise ValueError("Private/lokale IP-Adressen sind nicht erlaubt.")
    return value


class VoiceControls(discord.ui.View):
    def __init__(self, cog: "VoiceSuite", guild_id: int) -> None:
        super().__init__(timeout=300)
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

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.states: dict[int, PlaybackState] = {}
        self.idle_guard.start()

    async def cog_load(self) -> None:
        await self.bot.database.execute(
            """
            CREATE TABLE IF NOT EXISTS voice_soundboard (
                guild_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                audio_url TEXT NOT NULL,
                created_by INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(guild_id,name)
            )
            """
        )

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
        current = interaction.guild.voice_client
        if current and current.is_connected():
            if current.channel != interaction.user.voice.channel:
                try:
                    await current.move_to(interaction.user.voice.channel)
                except discord.HTTPException:
                    await interaction.response.send_message("Ich konnte nicht in deinen Voice-Channel wechseln.", ephemeral=True)
                    return None
            return current
        connected = sum(1 for voice in self.bot.voice_clients if voice.is_connected())
        if connected >= MAX_GLOBAL_VOICE:
            await interaction.response.send_message(
                f"Das globale Voice-Limit von **{MAX_GLOBAL_VOICE}** Sessions ist bereits erreicht.",
                ephemeral=True,
            )
            return None
        try:
            return await interaction.user.voice.channel.connect(timeout=15, reconnect=True)
        except (discord.ClientException, discord.HTTPException, asyncio.TimeoutError) as exc:
            logger.warning("Voice connect failed: %s", exc)
            await interaction.response.send_message(
                "Voice-Verbindung fehlgeschlagen. Prüfe **PyNaCl**, FFmpeg und die Discord-Voice-Rechte.",
                ephemeral=True,
            )
            return None

    async def _clear_state(self, guild_id: int) -> None:
        state = self.states.pop(guild_id, None)
        if state and state.temporary_file:
            try:
                Path(state.temporary_file).unlink(missing_ok=True)
            except OSError:
                pass

    def _after_playback(self, guild_id: int, error: Exception | None) -> None:
        if error:
            logger.warning("Voice playback error in guild %s: %s", guild_id, error)
        try:
            loop = self.bot.loop
            loop.call_soon_threadsafe(lambda: asyncio.create_task(self._clear_state(guild_id)))
        except RuntimeError:
            pass

    async def _start_source(
        self,
        interaction: discord.Interaction,
        source: discord.AudioSource,
        *,
        title: str,
        kind: str,
        temporary_file: str | None = None,
    ) -> bool:
        voice = await self._get_voice(interaction)
        if voice is None:
            if temporary_file:
                Path(temporary_file).unlink(missing_ok=True)
            return False
        if voice.is_playing() or voice.is_paused():
            voice.stop()
            await asyncio.sleep(0)
        await self._clear_state(interaction.guild_id or 0)
        gid = int(interaction.guild_id or 0)
        now = time.monotonic()
        self.states[gid] = PlaybackState(title, kind, now, interaction.user.id, now, temporary_file)
        try:
            voice.play(source, after=lambda error: self._after_playback(gid, error))
        except Exception:
            await self._clear_state(gid)
            raise
        return True

    def _lavfi_source(self, graph: str) -> discord.FFmpegPCMAudio:
        return discord.FFmpegPCMAudio(graph, before_options="-f lavfi", options="-vn -loglevel error")

    def _remote_source(self, url: str) -> discord.FFmpegPCMAudio:
        return discord.FFmpegPCMAudio(
            url,
            before_options="-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
            options="-vn -loglevel error",
        )

    async def play_named_sound(self, interaction: discord.Interaction, sound_name: str) -> None:
        if not self._ffmpeg_ready():
            await interaction.response.send_message("FFmpeg ist auf dem Pi nicht installiert.", ephemeral=True)
            return
        key = sound_name.strip().lower()
        builtin = BUILTIN_SOUNDS.get(key)
        if builtin:
            label, graph = builtin
            source = self._lavfi_source(graph)
            ok = await self._start_source(interaction, source, title=label, kind="Soundboard")
            if ok:
                if interaction.response.is_done():
                    await interaction.followup.send(f"🔊 **{label}**", ephemeral=True)
                else:
                    await interaction.response.send_message(f"🔊 **{label}**", ephemeral=True)
            return
        if interaction.guild_id is None:
            return
        row = await self.bot.database.fetchone(
            "SELECT audio_url FROM voice_soundboard WHERE guild_id=? AND lower(name)=lower(?)",
            (interaction.guild_id, key),
        )
        if not row:
            await interaction.response.send_message("Sound nicht gefunden. Nutze `/soundboard list`.", ephemeral=True)
            return
        source = self._remote_source(str(row["audio_url"]))
        ok = await self._start_source(interaction, source, title=key, kind="Soundboard")
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
        rows = await self.bot.database.fetchall(
            "SELECT name FROM voice_soundboard WHERE guild_id=? ORDER BY name COLLATE NOCASE LIMIT 15",
            (interaction.guild_id,),
        )
        entries = [(key, label) for key, (label, _) in BUILTIN_SOUNDS.items()]
        entries.extend((str(row["name"]), str(row["name"])) for row in rows)
        await interaction.response.send_message(
            embed=_embed("🔊 Soundboard", "Drücke einen Button. Der Bot tritt deinem aktuellen Voice-Channel bei."),
            view=SoundboardView(self, entries),
        )

    @soundboard.command(name="add", description="Fügt eine eigene HTTPS-Audiodatei zum Soundboard hinzu.")
    @app_commands.default_permissions(manage_guild=True)
    async def soundboard_add(self, interaction: discord.Interaction, name: str, url: str) -> None:
        if interaction.guild_id is None:
            return
        clean_name = name.strip().lower()[:40]
        if not clean_name or not all(ch.isalnum() or ch in "-_ " for ch in clean_name):
            await interaction.response.send_message("Der Name darf nur Buchstaben, Zahlen, Leerzeichen, `-` und `_` enthalten.", ephemeral=True)
            return
        try:
            clean_url = _safe_remote_audio_url(url)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await self.bot.database.execute(
            """
            INSERT INTO voice_soundboard(guild_id,name,audio_url,created_by) VALUES(?,?,?,?)
            ON CONFLICT(guild_id,name) DO UPDATE SET audio_url=excluded.audio_url,created_by=excluded.created_by,created_at=CURRENT_TIMESTAMP
            """,
            (interaction.guild_id, clean_name, clean_url, interaction.user.id),
        )
        await interaction.response.send_message(f"✅ Sound `{clean_name}` gespeichert.", ephemeral=True)

    @soundboard.command(name="remove", description="Entfernt einen eigenen Soundboard-Eintrag.")
    @app_commands.default_permissions(manage_guild=True)
    async def soundboard_remove(self, interaction: discord.Interaction, name: str) -> None:
        if interaction.guild_id is None:
            return
        await self.bot.database.execute(
            "DELETE FROM voice_soundboard WHERE guild_id=? AND lower(name)=lower(?)",
            (interaction.guild_id, name.strip()),
        )
        await interaction.response.send_message("Soundboard-Eintrag entfernt.", ephemeral=True)

    @soundboard.command(name="list", description="Zeigt alle verfügbaren Sounds.")
    async def soundboard_list(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            return
        rows = await self.bot.database.fetchall(
            "SELECT name FROM voice_soundboard WHERE guild_id=? ORDER BY name COLLATE NOCASE LIMIT 50",
            (interaction.guild_id,),
        )
        builtin = ", ".join(f"`{name}`" for name in BUILTIN_SOUNDS)
        custom = ", ".join(f"`{row['name']}`" for row in rows) or "—"
        await interaction.response.send_message(
            embed=_embed("🔊 Sounds", f"**Built-in**\n{builtin}\n\n**Eigene Sounds**\n{custom}"),
            ephemeral=True,
        )

    @app_commands.command(name="ambient", description="Spielt leichtgewichtige prozedurale Hintergrundatmosphäre im Voice-Channel.")
    @app_commands.choices(
        szene=[app_commands.Choice(name=label, value=key) for key, (label, _) in AMBIENT_FILTERS.items()]
    )
    async def ambient(self, interaction: discord.Interaction, szene: app_commands.Choice[str]) -> None:
        if not self._ffmpeg_ready():
            await interaction.response.send_message("FFmpeg ist auf dem Pi nicht installiert.", ephemeral=True)
            return
        label, graph = AMBIENT_FILTERS[szene.value]
        ok = await self._start_source(interaction, self._lavfi_source(graph), title=label, kind="Ambient")
        if ok:
            await interaction.response.send_message(
                embed=_embed(f"🌧️ Ambient · {label}", "Endlosschleife läuft. `/nowplaying` öffnet Pause/Stop/Disconnect."),
                ephemeral=True,
            )

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
            source = discord.FFmpegPCMAudio(temp_path, options="-vn -loglevel error")
            ok = await self._start_source(interaction, source, title=clean[:80], kind="TTS", temporary_file=temp_path)
        except Exception as exc:
            Path(temp_path).unlink(missing_ok=True)
            logger.warning("TTS failed: %s", exc)
            await interaction.followup.send("TTS konnte nicht erzeugt werden. Prüfe Sprache und Internetverbindung.", ephemeral=True)
            return
        if ok:
            await interaction.followup.send("🗣️ TTS wird abgespielt. Der Text wird über den externen gTTS-Dienst erzeugt.", ephemeral=True)

    @app_commands.command(name="nowplaying", description="Zeigt die aktuelle Voice-Wiedergabe mit Controls.")
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
        await interaction.response.send_message(
            embed=_embed(
                "🎧 Now Playing",
                f"**{state.title}**\nTyp: `{state.kind}` · Status: **{status}**\nLaufzeit: **{elapsed // 60}:{elapsed % 60:02d}**\nGestartet von <@{state.started_by}>",
            ),
            view=VoiceControls(self, interaction.guild_id),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @tasks.loop(seconds=60)
    async def idle_guard(self) -> None:
        now = time.monotonic()
        for voice in list(self.bot.voice_clients):
            if not voice.guild:
                continue
            gid = voice.guild.id
            state = self.states.get(gid)
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
