from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from modals.suggestion import SuggestionModal


class Suggestions(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="suggest", description="Submit a suggestion using an interactive modal.")
    @app_commands.guild_only()
    @app_commands.checks.cooldown(3, 60.0, key=lambda interaction: interaction.user.id)
    async def suggest(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(SuggestionModal(self.bot))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Suggestions(bot))
