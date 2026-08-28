from __future__ import annotations

import discord

from helpers.embeds import EmbedFactory


class PrioritySelect(discord.ui.Select):
    def __init__(self, bot: object) -> None:
        self.bot = bot
        super().__init__(
            placeholder="Select ticket priority…",
            options=[
                discord.SelectOption(label="Low", value="low", emoji="🟢"),
                discord.SelectOption(label="Normal", value="normal", emoji="🟡"),
                discord.SelectOption(label="High", value="high", emoji="🟠"),
                discord.SelectOption(label="Urgent", value="urgent", emoji="🔴"),
                discord.SelectOption(label="Critical", value="critical", emoji="🟣"),
            ],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        try:
            message = await self.bot.ticket_service.set_priority(interaction, self.values[0])
        except (RuntimeError, PermissionError, ValueError) as exc:
            await interaction.response.send_message(
                embed=EmbedFactory.error(title="Priority update failed", description=str(exc)), ephemeral=True
            )
            return
        await interaction.response.edit_message(
            embed=EmbedFactory.success(title="Priority updated", description=message), view=None
        )


class PrioritySelectView(discord.ui.View):
    def __init__(self, bot: object) -> None:
        super().__init__(timeout=60)
        self.add_item(PrioritySelect(bot))
