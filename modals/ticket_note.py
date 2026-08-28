from __future__ import annotations

import discord

from helpers.embeds import EmbedFactory


class TicketNoteModal(discord.ui.Modal, title="Internal Ticket Note"):
    note = discord.ui.TextInput(
        label="Note",
        placeholder="Only ticket staff can add notes. Notes are stored in the database.",
        style=discord.TextStyle.paragraph,
        max_length=2000,
    )

    def __init__(self, bot: object) -> None:
        super().__init__(timeout=300)
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            text = await self.bot.ticket_service.add_note(interaction, str(self.note))
        except (RuntimeError, PermissionError) as exc:
            await interaction.response.send_message(embed=EmbedFactory.error(title="Note failed", description=str(exc)), ephemeral=True)
            return
        await interaction.response.send_message(embed=EmbedFactory.success(title="Note saved", description=text), ephemeral=True)
