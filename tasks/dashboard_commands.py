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


class DashboardCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.task = None

    async def cog_load(self):
        self.task = asyncio.create_task(self.loop(), name="dashboard-command-queue")

    async def cog_unload(self):
        if self.task:
            self.task.cancel()

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
                    elif action == "send-embed":
                        channel = self.bot.get_channel(int(payload["channel_id"]))
                        if not isinstance(channel, discord.abc.Messageable):
                            raise ValueError("Channel not found")
                        embed = discord.Embed(
                            title=str(payload.get("title", ""))[:256],
                            description=str(payload.get("text", ""))[:4096],
                            color=_embed_color(payload.get("color")),
                        )
                        await channel.send(embed=embed)
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
                    else:
                        raise ValueError("Unsupported dashboard bot action")
                    status = "done"
                except Exception as exc:
                    status = "failed"
                    result = f"{type(exc).__name__}: {exc}"
                await self.bot.database.execute(
                    "UPDATE dashboard_commands SET status=?,result=?,processed_at=CURRENT_TIMESTAMP WHERE id=?",
                    (status, result, row["id"]),
                )
            await asyncio.sleep(2)


async def setup(bot):
    await bot.add_cog(DashboardCommands(bot))
