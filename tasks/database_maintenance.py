from __future__ import annotations
import asyncio
from datetime import datetime, UTC
from discord.ext import commands
from services.backups import BackupService

class DatabaseMaintenance(commands.Cog):
    def __init__(self,bot):self.bot=bot;self.task=None;self.last_daily=None;self.last_weekly=None;self.backups=BackupService(bot.database)
    async def cog_load(self):self.task=asyncio.create_task(self.loop(),name="db-maintenance")
    async def cog_unload(self):
        if self.task:self.task.cancel()
    async def loop(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            now=datetime.now(UTC)
            day=now.date().isoformat()
            if now.hour>=3 and self.last_daily!=day:
                try:
                    await self.bot.database.execute("PRAGMA optimize")
                    await self.bot.database.connection.execute("PRAGMA wal_checkpoint(PASSIVE)")
                    await self.bot.database.connection.commit()
                    await self.backups.create(kind="daily")
                    self.last_daily=day
                    if now.weekday()==0 and self.last_weekly!=day:
                        await self.backups.create(kind="weekly");self.last_weekly=day
                except Exception: pass
            await asyncio.sleep(1800)
async def setup(bot):await bot.add_cog(DatabaseMaintenance(bot))
