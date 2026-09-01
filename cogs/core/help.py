from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from helpers.embeds import EmbedFactory

HELP_CATEGORIES = {
    "core": ("Core", "🏠", [
        "/ping", "/status", "/botinfo", "/userinfo", "/serverinfo", "/avatar",
        "/roleinfo", "/channelinfo", "/permissions", "/commandinfo", "/timestamp",
        "/snowflake", "/membercount", "/servericon", "/invite", "/help",
    ]),
    "tickets": ("Tickets", "🎫", [
        "/ticket panel", "/ticket info", "/ticket queue", "/ticket claim", "/ticket unclaim",
        "/ticket priority", "/ticket add", "/ticket remove", "/ticket notes", "/ticket rename",
        "/ticket transfer", "/ticket transcript", "/ticket reopen", "/ticket delete",
    ]),
    "moderation": ("Moderation", "🛡️", [
        "/mod warn", "/mod warnings", "/mod case", "/mod unwarn", "/mod timeout",
        "/mod untimeout", "/mod kick", "/mod ban", "/mod unban", "/mod clear",
        "/mod lock", "/mod unlock", "/mod slowmode",
    ]),
    "community": ("Community", "💬", [
        "/suggest", "/poll", "/reminder create", "/reminder list", "/reminder cancel",
        "Welcome messages and auto roles",
    ]),
    "personnel": ("MD Personnel", "📊", [
        "/perso graph", "/perso list", "/perso render", "/perso delete", "/perso help",
    ]),
    "system": ("System", "🖥️", [
        "/system now", "/system health", "/system memory", "/system storage",
        "/system pihole", "/system graph", "/system setup", "/system config",
        "/system thresholds", "/system disable",
    ]),
    "management": ("Management", "🔧", [
        "/manage role-add", "/manage role-remove", "/manage nickname", "/manage announce",
        "/setup tickets", "/setup staff-add", "/setup staff-remove", "/setup welcome",
        "/setup autorole", "/setup welcome-message", "/setup welcome-preview",
        "/setup welcome-placeholders", "/setup suggestions", "/setup logs", "/setup show",
    ]),
    "owner": ("Bot Owner", "🔐", [
        "/system dashboard", "/system network", "/system processes", "/dev dashboard",
        "/dev diagnostics", "/dev memory", "/dev extensions", "/dev reload", "/dev load",
        "/dev unload", "/dev logs", "/dev command-stats", "/dev database-stats",
        "/dev database-optimize", "/dev cache-stats", "/dev cache-clear", "/dev gc", "/dev sync",
    ]),
}


def category_embed(key: str) -> discord.Embed:
    label, emoji, commands_list = HELP_CATEGORIES[key]
    description = "\n".join(
        f"• `{item}`" if item.startswith("/") else f"• {item}"
        for item in commands_list
    )
    return EmbedFactory.info(title=f"{emoji} {label}", description=description)


class HelpSelect(discord.ui.Select):
    def __init__(self) -> None:
        options = [
            discord.SelectOption(label=label, value=key, emoji=emoji, description=f"View {label.lower()} commands")
            for key, (label, emoji, _) in HELP_CATEGORIES.items()
        ]
        super().__init__(placeholder="Choose a category…", options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(embed=category_embed(self.values[0]), view=self.view)


class HelpView(discord.ui.View):
    def __init__(self, author_id: int) -> None:
        super().__init__(timeout=180)
        self.author_id = author_id
        self.add_item(HelpSelect())

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.author_id:
            return True
        await interaction.response.send_message("Open your own `/help` menu.", ephemeral=True)
        return False


class Help(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="help", description="Open the interactive Raspberry-Bot help center.")
    async def help_command(self, interaction: discord.Interaction) -> None:
        embed = EmbedFactory.info(
            title="Raspberry-Bot Help Center",
            description="Choose a category below. Commands are permission-aware and server configuration is stored per guild.",
        )
        await interaction.response.send_message(embed=embed, view=HelpView(interaction.user.id))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Help(bot))
