from __future__ import annotations
import discord
from discord import app_commands
from discord.ext import commands
from helpers.embeds import EmbedFactory
class Audit(commands.GroupCog,group_name="audit",group_description="Raspberry-Bot audit trail"):
    def __init__(self,bot):self.bot=bot
    @app_commands.command(name="recent",description="Recent bot configuration/personnel/admin changes.")
    @app_commands.default_permissions(manage_guild=True)
    async def recent(self,i:discord.Interaction,limit:app_commands.Range[int,1,25]=15):
        rows=await self.bot.database.fetchall("SELECT * FROM bot_audit_log WHERE guild_id=? ORDER BY id DESC LIMIT ?",(i.guild_id,limit))
        lines=[f"**#{r['id']} {r['action']}** · <@{r['actor_id']}>\n└ `{r['created_at']}` · {r['target_type'] or '—'} `{r['target_id'] or '—'}`" for r in rows]
        await i.response.send_message(embed=EmbedFactory.info(title="Audit Trail",description="\n\n".join(lines) or "No entries."),ephemeral=True)
async def setup(bot):await bot.add_cog(Audit(bot))
