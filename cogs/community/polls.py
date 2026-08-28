from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from modals.poll import PollModal


class Polls(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="poll", description="Create an interactive poll with up to four options.")
    @app_commands.guild_only()
    @app_commands.checks.cooldown(2, 60.0, key=lambda interaction: interaction.user.id)
    async def poll(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(PollModal(self.bot))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Polls(bot))
