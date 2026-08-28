from __future__ import annotations

import discord

from helpers.embeds import EmbedFactory
from modals.ticket_close import TicketCloseModal
from modals.ticket_note import TicketNoteModal
from views.tickets.priority import PrioritySelectView


class TicketControlsView(discord.ui.View):
    def __init__(self, bot: object) -> None:
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label="Claim", emoji="🙋", style=discord.ButtonStyle.success, custom_id="raspberry_bot:ticket:claim")
    async def claim(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        try:
            text = await self.bot.ticket_service.claim(interaction)
        except (RuntimeError, PermissionError) as exc:
            await interaction.response.send_message(embed=EmbedFactory.error(title="Claim failed", description=str(exc)), ephemeral=True)
            return
        await interaction.response.send_message(embed=EmbedFactory.success(title="Ticket claimed", description=text))

    @discord.ui.button(label="Unclaim", emoji="↩️", style=discord.ButtonStyle.secondary, custom_id="raspberry_bot:ticket:unclaim")
    async def unclaim(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        try:
            text = await self.bot.ticket_service.unclaim(interaction)
        except (RuntimeError, PermissionError) as exc:
            await interaction.response.send_message(embed=EmbedFactory.error(title="Unclaim failed", description=str(exc)), ephemeral=True)
            return
        await interaction.response.send_message(embed=EmbedFactory.success(title="Ticket unclaimed", description=text))

    @discord.ui.button(label="Priority", emoji="🚦", style=discord.ButtonStyle.primary, custom_id="raspberry_bot:ticket:priority")
    async def priority(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        try:
            await self.bot.ticket_service.require_staff(interaction)
            await self.bot.ticket_service.require_ticket(interaction.channel)
        except (RuntimeError, PermissionError) as exc:
            await interaction.response.send_message(embed=EmbedFactory.error(title="Not available", description=str(exc)), ephemeral=True)
            return
        await interaction.response.send_message(
            embed=EmbedFactory.ticket(title="Set priority", description="Choose the appropriate priority for this ticket."),
            view=PrioritySelectView(self.bot),
            ephemeral=True,
        )

    @discord.ui.button(label="Internal Note", emoji="📝", style=discord.ButtonStyle.secondary, custom_id="raspberry_bot:ticket:note")
    async def note(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        try:
            await self.bot.ticket_service.require_staff(interaction)
        except (RuntimeError, PermissionError) as exc:
            await interaction.response.send_message(embed=EmbedFactory.error(title="Not available", description=str(exc)), ephemeral=True)
            return
        await interaction.response.send_modal(TicketNoteModal(self.bot))

    @discord.ui.button(label="Close", emoji="🔒", style=discord.ButtonStyle.danger, custom_id="raspberry_bot:ticket:close")
    async def close(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(TicketCloseModal(self.bot))
