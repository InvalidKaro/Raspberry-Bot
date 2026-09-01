from __future__ import annotations
import asyncio, json
from discord.ext import commands
from services.pihole import collect_pihole_stats

class SystemHistory(commands.Cog):
    def __init__(self,bot): self.bot=bot; self.task=None
    async def cog_load(self): self.task=asyncio.create_task(self.loop(),name="system-history-v4")
    async def cog_unload(self):
        if self.task:self.task.cancel()
    async def loop(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                snap=await self.bot.system_metrics.get()
                for guild in self.bot.guilds:
                    pihole=await collect_pihole_stats()
                    tailscale=False
                    try:
                        proc=await asyncio.create_subprocess_exec("tailscale","status","--json",stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
                        out,_=await asyncio.wait_for(proc.communicate(),timeout=4); tailscale=proc.returncode==0
                    except Exception: pass
                    await self.bot.database.execute("""INSERT INTO system_snapshots_v4
                    (guild_id,cpu_percent,ram_percent,temperature,disk_percent,pihole_ok,tailscale_ok,extra_json)
                    VALUES(?,?,?,?,?,?,?,?)""",(guild.id,snap.cpu_percent,snap.ram_percent,snap.temperature,snap.disk_percent,1 if pihole.active else 0,1 if tailscale else 0,json.dumps({"load":snap.load_1m})))
                await self.bot.database.execute("DELETE FROM system_snapshots_v4 WHERE recorded_at < datetime('now','-8 days')")
            except Exception: pass
            await asyncio.sleep(30)
async def setup(bot):await bot.add_cog(SystemHistory(bot))
