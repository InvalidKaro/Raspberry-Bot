from __future__ import annotations

import asyncio
import json

import discord
from discord.ext import commands

from services.maintenance import collect_garbage


def _embed_color(value: str | None) -> int:
    raw = str(value or "").strip().lower().removeprefix("#").removeprefix("0x")
    try:
        return int(raw, 16)
    except ValueError:
        return discord.Color.blurple().value


def _clean_url(value: object) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if not raw.startswith(("https://", "http://")):
        raise ValueError("Image/thumbnail URLs must start with http:// or https://")
    return raw


class DashboardCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.task = None

    async def cog_load(self):
        self.task = asyncio.create_task(self.loop(), name="dashboard-command-queue")

    async def cog_unload(self):
        if self.task:
            self.task.cancel()

    async def _youtube_action(self, action: str, payload: dict) -> str:
        youtube = self.bot.get_cog("YouTubeSuite")
        voice_cog = self.bot.get_cog("VoiceSuite")
        if youtube is None or voice_cog is None:
            raise RuntimeError("YouTubeSuite/VoiceSuite is not loaded")
        guild_id = int(payload["guild_id"])
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise ValueError("Guild not found")

        if action == "ops-youtube-play":
            channel = guild.get_channel(int(payload["channel_id"]))
            if not isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
                raise ValueError("Voice channel not found")
            resolved = await youtube._resolve(str(payload["query"]), 0)
            voice = await voice_cog._connect_channel(guild, channel)
            await voice_cog._start_on_voice(
                voice,
                youtube._audio_source(resolved),
                guild_id=guild_id,
                title=resolved.track.title,
                kind="YouTube",
                started_by=0,
                source_name=resolved.track.webpage_url,
                volume=max(10, min(120, int(payload.get("volume", 65)))),
            )
            youtube.current[guild_id] = resolved.track
            youtube.session_active.add(guild_id)
            return f"YouTube started: {resolved.track.title}"

        if action == "ops-youtube-add":
            queue = youtube.queues[guild_id]
            if len(queue) >= 25:
                raise ValueError("YouTube queue is full")
            requested_raw = str(payload.get("requested_by") or "0")
            requested_by = int(requested_raw) if requested_raw.isdigit() else 0
            resolved = await youtube._resolve(str(payload["query"]), requested_by)
            queue.append(resolved.track)
            return f"Queued: {resolved.track.title} ({len(queue)})"

        voice = guild.voice_client
        if action == "ops-youtube-skip":
            if voice is None or not (voice.is_playing() or voice.is_paused()):
                raise ValueError("Nothing is playing")
            voice.stop()
            youtube.current.pop(guild_id, None)
            return "Skipped current YouTube track"
        if action == "ops-youtube-stop":
            youtube.session_active.discard(guild_id)
            youtube.current.pop(guild_id, None)
            if voice and (voice.is_playing() or voice.is_paused()):
                voice.stop()
            return "YouTube session stopped"
        if action == "ops-youtube-pause":
            if voice is None or not voice.is_playing():
                raise ValueError("Nothing is playing")
            voice.pause()
            return "YouTube paused"
        if action == "ops-youtube-resume":
            if voice is None or not voice.is_paused():
                raise ValueError("Nothing is paused")
            voice.resume()
            return "YouTube resumed"
        if action == "ops-youtube-volume":
            value = voice_cog.set_session_volume(guild_id, int(payload.get("volume", 65)))
            return f"Voice volume -> {value}%"
        if action == "ops-youtube-reorder":
            queue = youtube.queues[guild_id]
            items = list(queue)
            if not items:
                raise ValueError("Queue is empty")
            source = max(0, min(len(items) - 1, int(payload.get("from", 0))))
            target = max(0, min(len(items) - 1, int(payload.get("to", 0))))
            item = items.pop(source)
            items.insert(target, item)
            queue.clear()
            queue.extend(items)
            return f"Queue item moved {source + 1} -> {target + 1}"
        if action == "ops-youtube-clear":
            count = len(youtube.queues[guild_id])
            youtube.queues[guild_id].clear()
            return f"Cleared {count} YouTube queue items"
        if action == "ops-youtube-mod":
            user_id = int(payload["user_id"])
            enabled = bool(payload.get("enabled", True))
            if enabled:
                await self.bot.database.execute(
                    "INSERT OR REPLACE INTO youtube_queue_mods(guild_id,user_id,added_by,created_at) VALUES(?,?,0,CURRENT_TIMESTAMP)",
                    (guild_id, user_id),
                )
            else:
                await self.bot.database.execute(
                    "DELETE FROM youtube_queue_mods WHERE guild_id=? AND user_id=?",
                    (guild_id, user_id),
                )
            return f"YouTube mod {user_id} -> {'enabled' if enabled else 'disabled'}"
        raise ValueError("Unsupported YouTube dashboard action")

    async def loop(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            rows = await self.bot.database.fetchall(
                "SELECT * FROM dashboard_commands WHERE status='pending' ORDER BY id LIMIT 5"
            )
            for row in rows:
                result = ""
                try:
                    action = str(row["action"])
                    payload = json.loads(row["payload_json"] or "{}")
                    if action == "sync":
                        synced = await self.bot.tree.sync()
                        result = f"Synced {len(synced)} commands"
                    elif action == "reload":
                        ext = str(payload["extension"])
                        await self.bot.reload_extension(ext)
                        result = f"Reloaded {ext}"
                    elif action == "load":
                        ext = str(payload["extension"])
                        await self.bot.load_extension(ext)
                        result = f"Loaded {ext}"
                    elif action == "unload":
                        ext = str(payload["extension"])
                        await self.bot.unload_extension(ext)
                        result = f"Unloaded {ext}"
                    elif action == "cache-clear":
                        cleared = await self.bot.cache.clear_all()
                        result = f"Cleared {sum(cleared.values())} cache entries"
                    elif action == "gc":
                        gc_result = await asyncio.to_thread(collect_garbage)
                        freed = max(gc_result.before_mb - gc_result.after_mb, 0)
                        result = (
                            f"GC collected {gc_result.collected_objects} objects; "
                            f"RSS {gc_result.before_mb:.1f} -> {gc_result.after_mb:.1f} MB "
                            f"({freed:.1f} MB difference)"
                        )
                    elif action == "database-optimize":
                        await self.bot.database.optimize()
                        result = "SQLite PRAGMA optimize completed"
                    elif action == "send-message":
                        channel = self.bot.get_channel(int(payload["channel_id"]))
                        if not isinstance(channel, discord.abc.Messageable):
                            raise ValueError("Channel not found")
                        await channel.send(str(payload["text"])[:1900])
                        result = f"Message sent to {payload['channel_id']}"
                    elif action in {"send-embed", "send-embed-v2"}:
                        channel = self.bot.get_channel(int(payload["channel_id"]))
                        if not isinstance(channel, discord.abc.Messageable):
                            raise ValueError("Channel not found")
                        embed = discord.Embed(
                            title=str(payload.get("title", ""))[:256] or None,
                            description=str(payload.get("text", ""))[:4096] or None,
                            color=_embed_color(payload.get("color")),
                        )
                        if action == "send-embed-v2":
                            author = str(payload.get("author", "")).strip()
                            footer = str(payload.get("footer", "")).strip()
                            thumbnail = _clean_url(payload.get("thumbnail"))
                            image = _clean_url(payload.get("image"))
                            if author:
                                embed.set_author(name=author[:256])
                            if footer:
                                embed.set_footer(text=footer[:2048])
                            if thumbnail:
                                embed.set_thumbnail(url=thumbnail)
                            if image:
                                embed.set_image(url=image)
                            fields = payload.get("fields") or []
                            if not isinstance(fields, list):
                                raise ValueError("fields must be a list")
                            for item in fields[:25]:
                                if not isinstance(item, dict):
                                    continue
                                name = str(item.get("name", "")).strip()[:256]
                                value = str(item.get("value", "")).strip()[:1024]
                                if name and value:
                                    embed.add_field(name=name, value=value, inline=bool(item.get("inline")))
                        await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
                        result = f"Embed sent to {payload['channel_id']}"
                    elif action == "plugin-toggle":
                        ext = str(payload["extension"])
                        enabled = bool(payload["enabled"])
                        if not ext.startswith("cogs."):
                            raise ValueError("Only cogs.* plugins are supported")
                        if ext == "cogs.management.automation_suite" and not enabled:
                            raise ValueError("Automation Suite cannot disable itself")
                        await self.bot.database.execute(
                            """
                            INSERT INTO plugin_state(extension,enabled,updated_by)
                            VALUES(?,?,0)
                            ON CONFLICT(extension) DO UPDATE SET
                                enabled=excluded.enabled,updated_at=CURRENT_TIMESTAMP
                            """,
                            (ext, int(enabled)),
                        )
                        if enabled and ext not in self.bot.extensions:
                            await self.bot.load_extension(ext)
                        elif not enabled and ext in self.bot.extensions:
                            await self.bot.unload_extension(ext)
                        result = f"{ext} -> {'enabled' if enabled else 'disabled'}"
                    elif action.startswith("media-"):
                        voice_cog = self.bot.get_cog("VoiceSuite")
                        if voice_cog is None:
                            raise RuntimeError("VoiceSuite is not loaded")
                        guild_id = int(payload["guild_id"])
                        if action == "media-radio-play":
                            result = await voice_cog.dashboard_play_radio(
                                guild_id,
                                int(payload["channel_id"]),
                                str(payload["station"]),
                                int(payload.get("volume", 65)),
                            )
                        elif action == "media-ambient-play":
                            result = await voice_cog.dashboard_play_ambient(
                                guild_id,
                                int(payload["channel_id"]),
                                str(payload["scene"]),
                                int(payload.get("volume", 65)),
                                int(payload.get("minutes", 0)),
                            )
                        elif action == "media-ambient-source-play":
                            result = await voice_cog.dashboard_play_ambient_source(
                                guild_id,
                                int(payload["channel_id"]),
                                str(payload["source"]),
                                int(payload.get("volume", 65)),
                                int(payload.get("minutes", 0)),
                            )
                        elif action == "media-spotify-play":
                            spotify = self.bot.get_cog("SpotifySuite")
                            if spotify is None:
                                raise RuntimeError("SpotifySuite is not loaded")
                            result = await spotify.dashboard_play(
                                guild_id,
                                int(payload["channel_id"]),
                                str(payload["source"]),
                                int(payload.get("volume", 65)),
                            )
                        elif action == "media-spotify-add":
                            spotify = self.bot.get_cog("SpotifySuite")
                            if spotify is None:
                                raise RuntimeError("SpotifySuite is not loaded")
                            result = await spotify.dashboard_add(
                                guild_id,
                                str(payload["source"]),
                                int(payload.get("requested_by", 0) or 0),
                            )
                        elif action == "media-volume":
                            volume = voice_cog.set_session_volume(guild_id, int(payload.get("volume", 65)))
                            result = f"Voice volume -> {volume}%"
                        elif action == "media-stop":
                            result = await voice_cog.dashboard_stop(guild_id)
                        elif action == "media-disconnect":
                            result = await voice_cog.dashboard_disconnect(guild_id)
                        else:
                            raise ValueError("Unsupported media action")
                    elif action.startswith("ops-youtube-"):
                        result = await self._youtube_action(action, payload)
                    else:
                        raise ValueError("Unsupported dashboard bot action")
                    status = "done"
                except Exception as exc:
                    status = "failed"
                    result = f"{type(exc).__name__}: {exc}"
                    telemetry = self.bot.get_cog("DashboardTelemetry")
                    if telemetry is not None:
                        await telemetry.activity(
                            None,
                            "dashboard_error",
                            action if 'action' in locals() else "dashboard-command",
                            detail=result,
                            source="tasks.dashboard_commands",
                        )
                await self.bot.database.execute(
                    "UPDATE dashboard_commands SET status=?,result=?,processed_at=CURRENT_TIMESTAMP WHERE id=?",
                    (status, result, row["id"]),
                )
            await asyncio.sleep(2)


async def setup(bot):
    await bot.add_cog(DashboardCommands(bot))