from __future__ import annotations
import discord
from discord import app_commands
from discord.ext import commands
from helpers.embeds import EmbedFactory

class Analytics(commands.GroupCog,group_name="analytics",group_description="Command performance analytics"):
    def __init__(self,bot):self.bot=bot
    @app_commands.command(name="commands",description="Most used commands, errors and average runtime.")
    @app_commands.default_permissions(manage_guild=True)
    async def commands(self,i:discord.Interaction,hours:app_commands.Range[int,1,168]=24):
        rows=await self.bot.database.fetchall("""SELECT command_name,COUNT(*) uses,SUM(CASE WHEN success=0 THEN 1 ELSE 0 END) errors,
        AVG(duration_ms) avg_ms,MAX(duration_ms) max_ms FROM command_analytics
        WHERE created_at>=datetime('now',?) AND (guild_id=? OR ? IS NULL) GROUP BY command_name ORDER BY uses DESC LIMIT 20""",(f"-{hours} hours",i.guild_id,i.guild_id))
        lines=[f"**/{r['command_name']}** · {r['uses']} uses · {r['errors']} err · Ø {float(r['avg_ms'] or 0):.0f}ms · max {float(r['max_ms'] or 0):.0f}ms" for r in rows]
        await i.response.send_message(embed=EmbedFactory.info(title=f"Command Analytics • {hours}h",description="\n".join(lines) or "No data."),ephemeral=True)
    @app_commands.command(name="slow",description="Show slowest command executions.")
    @app_commands.default_permissions(manage_guild=True)
    async def slow(self,i):
        rows=await self.bot.database.fetchall("""SELECT command_name,duration_ms,created_at FROM command_analytics WHERE guild_id=? AND duration_ms IS NOT NULL ORDER BY duration_ms DESC LIMIT 15""",(i.guild_id,))
        await i.response.send_message(embed=EmbedFactory.info(title="Slow Commands",description="\n".join(f"**/{r['command_name']}** · {r['duration_ms']:.0f}ms · `{r['created_at']}`" for r in rows) or "No data."),ephemeral=True)
async def setup(bot):await bot.add_cog(Analytics(bot))
