from __future__ import annotations
import logging, discord
from discord import app_commands
from discord.ext import commands
from helpers.embeds import EmbedFactory
from services.discord_log_forwarder import DiscordLogForwarder

class BotLog(commands.GroupCog,group_name="botlog",group_description="Discord bot log routing"):
    def __init__(self,bot):self.bot=bot;self.fwd=DiscordLogForwarder(bot)
    async def cog_load(self): await self.fwd.start(); self.bot.discord_log_forwarder=self.fwd
    async def cog_unload(self): await self.fwd.stop()
    async def interaction_check(self,i):
        if i.user.id in self.bot.settings.owner_ids:return True
        await i.response.send_message("Owner only.",ephemeral=True);return False

    @app_commands.command(name="setup",description="Route INFO/WARNING/ERROR into separate Discord channels.")
    async def setup(self,i:discord.Interaction,info:discord.TextChannel|None=None,warning:discord.TextChannel|None=None,error:discord.TextChannel|None=None):
        if not any((info,warning,error)):
            await i.response.send_message("Choose at least one channel.",ephemeral=True);return
        await self.bot.database.execute("""INSERT INTO discord_log_routes(guild_id,info_channel_id,warning_channel_id,error_channel_id,enabled)
        VALUES(?,?,?,?,1) ON CONFLICT(guild_id) DO UPDATE SET info_channel_id=excluded.info_channel_id,warning_channel_id=excluded.warning_channel_id,error_channel_id=excluded.error_channel_id,enabled=1,updated_at=CURRENT_TIMESTAMP""",(i.guild_id,info.id if info else None,warning.id if warning else None,error.id if error else None))
        await self.fwd.load()
        await i.response.send_message(embed=EmbedFactory.success(title="Log routing enabled",description=f"INFO: {info.mention if info else '—'}\nWARNING: {warning.mention if warning else '—'}\nERROR: {error.mention if error else '—'}"),ephemeral=True)

    @app_commands.command(name="status",description="Show log routing.")
    async def status(self,i):
        r=await self.bot.database.fetchone("SELECT * FROM discord_log_routes WHERE guild_id=?",(i.guild_id,))
        await i.response.send_message(embed=EmbedFactory.info(title="Log Routing",description=(f"INFO <#{r['info_channel_id']}>\nWARNING <#{r['warning_channel_id']}>\nERROR <#{r['error_channel_id']}>" if r else "Disabled")),ephemeral=True)

    @app_commands.command(name="test",description="Send test INFO/WARNING/ERROR records.")
    async def test(self,i):
        l=logging.getLogger("raspberry_bot.test");l.info("INFO route test");l.warning("WARNING route test");l.error("ERROR route test [TEST0001]")
        await i.response.send_message("Tests queued.",ephemeral=True)

    @app_commands.command(name="disable",description="Disable Discord log routing.")
    async def disable(self,i):
        await self.bot.database.execute("UPDATE discord_log_routes SET enabled=0 WHERE guild_id=?",(i.guild_id,));await self.fwd.load()
        await i.response.send_message("Disabled.",ephemeral=True)
async def setup(bot):await bot.add_cog(BotLog(bot))
