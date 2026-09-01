from __future__ import annotations
import discord
from discord import app_commands
from discord.ext import commands
from helpers.embeds import EmbedFactory
from services.access_control import NAMES

class Access(commands.GroupCog,group_name="access",group_description="Raspberry-Bot permission roles"):
    def __init__(self,bot): self.bot=bot

    async def interaction_check(self,interaction):
        if interaction.user.id in self.bot.settings.owner_ids or (isinstance(interaction.user,discord.Member) and interaction.user.guild_permissions.administrator): return True
        await interaction.response.send_message(embed=EmbedFactory.error(title="Keine Berechtigung",description="Owner oder Server-Administrator erforderlich."),ephemeral=True); return False

    @app_commands.command(name="set",description="Bot-Berechtigungslevel an eine Discord-Rolle binden.")
    @app_commands.choices(level=[app_commands.Choice(name=x.title(),value=x) for x in ("ticketstaff","perso","moderator","admin")])
    async def set_role(self,interaction:discord.Interaction,rolle:discord.Role,level:app_commands.Choice[str]):
        await self.bot.database.execute("""INSERT INTO bot_access_roles(guild_id,role_id,level,created_by) VALUES(?,?,?,?)
        ON CONFLICT(guild_id,role_id) DO UPDATE SET level=excluded.level,created_by=excluded.created_by""",(interaction.guild_id,rolle.id,level.value,interaction.user.id))
        if hasattr(self.bot,"audit"): await self.bot.audit.record("access.role.set",guild_id=interaction.guild_id,actor_id=interaction.user.id,target_type="role",target_id=rolle.id,after={"level":level.value})
        await interaction.response.send_message(embed=EmbedFactory.success(title="Bot-Rolle gesetzt",description=f"{rolle.mention} → **{level.name}**"),ephemeral=True)

    @app_commands.command(name="remove",description="Bot-Berechtigungslevel von einer Rolle entfernen.")
    async def remove(self,interaction:discord.Interaction,rolle:discord.Role):
        await self.bot.database.execute("DELETE FROM bot_access_roles WHERE guild_id=? AND role_id=?",(interaction.guild_id,rolle.id))
        await interaction.response.send_message(embed=EmbedFactory.success(title="Bot-Rolle entfernt",description=rolle.mention),ephemeral=True)

    @app_commands.command(name="list",description="Konfigurierte Bot-Berechtigungsrollen anzeigen.")
    async def list_roles(self,interaction:discord.Interaction):
        rows=await self.bot.database.fetchall("SELECT role_id,level FROM bot_access_roles WHERE guild_id=? ORDER BY level DESC",(interaction.guild_id,))
        await interaction.response.send_message(embed=EmbedFactory.info(title="Bot Permissions",description="\n".join(f"<@&{r['role_id']}> → **{r['level']}**" for r in rows) or "Keine Rollen konfiguriert."),ephemeral=True)

async def setup(bot): await bot.add_cog(Access(bot))
