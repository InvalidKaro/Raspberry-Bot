from __future__ import annotations
import discord
from discord import app_commands
from discord.ext import commands
from helpers.embeds import EmbedFactory
from services.welcome_templates import render_welcome_template, placeholder_help_text

class RoleButton(discord.ui.Button):
    def __init__(self, role_id:int,label:str,emoji:str|None=None):
        super().__init__(label=label,emoji=emoji,style=discord.ButtonStyle.secondary,custom_id=f"rb:role:{role_id}")
        self.role_id=role_id
    async def callback(self,interaction):
        if not interaction.guild or not isinstance(interaction.user,discord.Member): return
        role=interaction.guild.get_role(self.role_id)
        if not role:
            await interaction.response.send_message("Rolle existiert nicht mehr.",ephemeral=True); return
        if role in interaction.user.roles:
            await interaction.user.remove_roles(role,reason="Self role button")
            msg=f"{role.mention} entfernt."
        else:
            await interaction.user.add_roles(role,reason="Self role button")
            msg=f"{role.mention} hinzugefügt."
        await interaction.response.send_message(msg,ephemeral=True)

class RuleButton(discord.ui.Button):
    def __init__(self,role_id:int):
        super().__init__(label="Regeln akzeptieren",emoji="✅",style=discord.ButtonStyle.success,custom_id=f"rb:rules:{role_id}")
        self.role_id=role_id
    async def callback(self,interaction):
        role=interaction.guild.get_role(self.role_id) if interaction.guild else None
        if role and isinstance(interaction.user,discord.Member):
            await interaction.user.add_roles(role,reason="Rules accepted")
            await interaction.response.send_message(f"Regeln akzeptiert. {role.mention} wurde vergeben.",ephemeral=True)
        else: await interaction.response.send_message("Rolle nicht verfügbar.",ephemeral=True)

class Onboarding(commands.GroupCog,group_name="onboarding",group_description="Welcome, Button-Rollen und Regelbestätigung"):
    def __init__(self,bot): self.bot=bot

    @app_commands.command(name="placeholders",description="Alle Welcome-Placeholder anzeigen.")
    async def placeholders(self,interaction):
        await interaction.response.send_message(embed=EmbedFactory.info(title="Welcome Placeholder",description=placeholder_help_text()),ephemeral=True)

    @app_commands.command(name="preview",description="Welcome-Nachricht mit einem Testmitglied rendern.")
    async def preview(self,interaction:discord.Interaction,text:str,mitglied:discord.Member|None=None):
        member=mitglied or interaction.user
        rendered=render_welcome_template(text,member,interaction.channel)
        await interaction.response.send_message(embed=EmbedFactory.info(title="Welcome Preview",description=rendered),ephemeral=True)

    @app_commands.command(name="role-button",description="Einfachen Button zum An-/Ablegen einer Rolle senden.")
    @app_commands.default_permissions(manage_roles=True)
    async def role_button(self,interaction:discord.Interaction,rolle:discord.Role,label:str="Rolle wählen",emoji:str|None=None,channel:discord.TextChannel|None=None):
        target=channel or interaction.channel
        v=discord.ui.View(timeout=None); v.add_item(RoleButton(rolle.id,label,emoji))
        msg=await target.send(embed=EmbedFactory.info(title="Rollen-Auswahl",description=f"Klicke auf den Button für {rolle.mention}."),view=v)
        await self.bot.database.execute("INSERT OR REPLACE INTO button_roles(guild_id,message_id,channel_id,role_id,label,emoji,created_by) VALUES(?,?,?,?,?,?,?)",(interaction.guild_id,msg.id,target.id,rolle.id,label,emoji,interaction.user.id))
        await interaction.response.send_message("Button-Rolle erstellt.",ephemeral=True)

    @app_commands.command(name="rules",description="Regelbestätigung mit automatischer Rolle erstellen.")
    @app_commands.default_permissions(manage_guild=True)
    async def rules(self,interaction:discord.Interaction,rolle:discord.Role,regeln:str,channel:discord.TextChannel|None=None):
        target=channel or interaction.channel
        v=discord.ui.View(timeout=None); v.add_item(RuleButton(rolle.id))
        msg=await target.send(embed=EmbedFactory.info(title="Regeln",description=regeln),view=v)
        await self.bot.database.execute("""INSERT OR REPLACE INTO onboarding_rules(guild_id,channel_id,role_id,message_id,rules_text,updated_by,updated_at)
        VALUES(?,?,?,?,?,?,CURRENT_TIMESTAMP)""",(interaction.guild_id,target.id,rolle.id,msg.id,regeln,interaction.user.id))
        await interaction.response.send_message("Regelbestätigung erstellt.",ephemeral=True)

async def setup(bot): await bot.add_cog(Onboarding(bot))
