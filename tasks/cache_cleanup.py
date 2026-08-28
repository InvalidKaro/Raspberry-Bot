from __future__ import annotations

import logging

from discord.ext import commands, tasks

logger = logging.getLogger(__name__)


class CacheCleanupTask(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.cleanup.start()

    def cog_unload(self) -> None:
        self.cleanup.cancel()

    @tasks.loop(minutes=10)
    async def cleanup(self) -> None:
        result = await self.bot.cache.expire_all()
        removed = sum(result.values())
        if removed:
            logger.debug("Expired %s cached entries", removed)

    @cleanup.before_loop
    async def before_cleanup(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CacheCleanupTask(bot))
