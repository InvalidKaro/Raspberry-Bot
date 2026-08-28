from __future__ import annotations

import discord

from helpers.embeds import EmbedFactory


class TicketCloseModal(discord.ui.Modal, title="Close Ticket"):
    reason = discord.ui.TextInput(
        label="Reason",
        placeholder="Why is this ticket being closed?",
        style=discord.TextStyle.paragraph,
        max_length=500,
        default="Resolved",
    )

    def __init__(self, bot: object) -> None:
        super().__init__(timeout=300)
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            text = await self.bot.ticket_service.close(interaction, str(self.reason))
        except (RuntimeError, PermissionError, discord.HTTPException) as exc:
            await interaction.followup.send(embed=EmbedFactory.error(title="Close failed", description=str(exc)), ephemeral=True)
            return
        await interaction.followup.send(embed=EmbedFactory.success(title="Ticket closed", description=text), ephemeral=True)
