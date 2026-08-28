from __future__ import annotations

import discord

from helpers.embeds import EmbedFactory


class TicketCreateModal(discord.ui.Modal, title="Create Support Ticket"):
    category = discord.ui.TextInput(
        label="Category",
        placeholder="e.g. Technical Support, Report, General",
        max_length=80,
        default="General Support",
    )
    subject = discord.ui.TextInput(
        label="Subject",
        placeholder="Short description of the issue",
        max_length=100,
    )
    description = discord.ui.TextInput(
        label="Description",
        placeholder="Describe your issue in as much useful detail as possible.",
        style=discord.TextStyle.paragraph,
        max_length=2000,
    )

    def __init__(self, bot: object) -> None:
        super().__init__(timeout=300)
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            channel = await self.bot.ticket_service.create_ticket(
                interaction,
                subject=str(self.subject),
                description=str(self.description),
                category_name=str(self.category),
            )
        except (RuntimeError, discord.HTTPException) as exc:
            await interaction.followup.send(
                embed=EmbedFactory.error(title="Ticket creation failed", description=str(exc)), ephemeral=True
            )
            return
        await interaction.followup.send(
            embed=EmbedFactory.success(title="Ticket created", description=f"Your ticket is ready: {channel.mention}"),
            ephemeral=True,
        )
