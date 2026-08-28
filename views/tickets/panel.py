from __future__ import annotations

import discord

from modals.ticket_create import TicketCreateModal


class TicketPanelView(discord.ui.View):
    def __init__(self, bot: object) -> None:
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(
        label="Create Ticket",
        emoji="🎫",
        style=discord.ButtonStyle.primary,
        custom_id="raspberry_bot:ticket:create",
    )
    async def create_ticket(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(TicketCreateModal(self.bot))
