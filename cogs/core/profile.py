from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from helpers.embeds import EmbedFactory
from services.profile_card import render_profile_card


class Profile(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="profile", description="Render a lightweight Pillow profile card.")
    @app_commands.guild_only()
    @app_commands.checks.cooldown(1, 10.0, key=lambda interaction: interaction.user.id)
    async def profile(self, interaction: discord.Interaction, member: discord.Member | None = None) -> None:
        target = member or interaction.user
        if not isinstance(target, discord.Member):
            return
        await interaction.response.defer(thinking=True)
        image = await render_profile_card(target)
        embed = EmbedFactory.info(title=f"Profile • {target.display_name}")
        embed.set_image(url="attachment://profile.png")
        await interaction.followup.send(embed=embed, file=discord.File(image, filename="profile.png"))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Profile(bot))
