from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from helpers.embeds import EmbedFactory

HELP_CATEGORIES = {
    "core": ("Core", "🏠", ["/ping", "/status", "/userinfo", "/serverinfo", "/avatar", "/help"]),
    "tickets": ("Tickets", "🎫", ["/ticket panel", "/ticket info", "/ticket queue", "/ticket add", "/ticket remove", "/ticket reopen", "/ticket delete"]),
    "moderation": ("Moderation", "🛡️", ["/mod warn", "/mod warnings", "/mod timeout", "/mod kick", "/mod ban", "/mod clear", "/mod lock", "/mod slowmode"]),
    "community": ("Community", "💬", ["/suggest", "/poll", "Welcome messages"]),
    "system": ("System", "🖥️", ["/system now", "/system setup", "/system graph", "/dev cache-stats", "/dev gc"]),
    "setup": ("Configuration", "⚙️", ["/setup tickets", "/setup staff-add", "/setup staff-remove", "/setup welcome", "/setup suggestions", "/setup show"]),
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
