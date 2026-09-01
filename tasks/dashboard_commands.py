from __future__ import annotations
import asyncio, json
from discord.ext import commands

class DashboardCommands(commands.Cog):
    def __init__(self,bot):self.bot=bot;self.task=None
    async def cog_load(self):self.task=asyncio.create_task(self.loop(),name="dashboard-command-queue")
    async def cog_unload(self):
        if self.task:self.task.cancel()
    async def loop(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            rows=await self.bot.database.fetchall("SELECT * FROM dashboard_commands WHERE status='pending' ORDER BY id LIMIT 5")
            for row in rows:
                result=""
                try:
                    action=str(row["action"]);payload=json.loads(row["payload_json"] or "{}")
                    if action=="sync":
                        synced=await self.bot.tree.sync();result=f"Synced {len(synced)} commands"
                    elif action=="reload":
                        ext=str(payload["extension"]);await self.bot.reload_extension(ext);result=f"Reloaded {ext}"
                    elif action=="load":
                        ext=str(payload["extension"]);await self.bot.load_extension(ext);result=f"Loaded {ext}"
                    elif action=="unload":
                        ext=str(payload["extension"]);await self.bot.unload_extension(ext);result=f"Unloaded {ext}"
                    else: raise ValueError("Unsupported dashboard bot action")
                    status="done"
                except Exception as exc:status="failed";result=f"{type(exc).__name__}: {exc}"
                await self.bot.database.execute("UPDATE dashboard_commands SET status=?,result=?,processed_at=CURRENT_TIMESTAMP WHERE id=?",(status,result,row["id"]))
            await asyncio.sleep(2)
async def setup(bot):await bot.add_cog(DashboardCommands(bot))
