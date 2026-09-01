from __future__ import annotations

import asyncio
import json
from discord.ext import commands
from services.maintenance import collect_garbage


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
