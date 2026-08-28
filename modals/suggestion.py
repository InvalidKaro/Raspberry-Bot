from __future__ import annotations

import discord

from helpers.embeds import EmbedFactory


class SuggestionModal(discord.ui.Modal, title="Submit Suggestion"):
    content = discord.ui.TextInput(
        label="Suggestion",
        placeholder="Describe your suggestion clearly and constructively.",
        style=discord.TextStyle.paragraph,
        max_length=1800,
    )

    def __init__(self, bot: object) -> None:
        super().__init__(timeout=300)
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        settings = await self.bot.settings_repo.get_guild_settings(interaction.guild.id)
        channel_id = settings.get("suggestion_channel_id")
        channel = interaction.guild.get_channel(int(channel_id)) if channel_id else None
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                embed=EmbedFactory.error(title="Suggestions not configured", description="An administrator must configure a suggestions channel first."),
                ephemeral=True,
            )
            return
        suggestion_id = await self.bot.database.execute(
            "INSERT INTO suggestions (guild_id, channel_id, author_id, content) VALUES (?, ?, ?, ?)",
            (interaction.guild.id, channel.id, interaction.user.id, str(self.content)),
        )
        from views.suggestions import SuggestionView

        embed = EmbedFactory.info(title=f"Suggestion #{suggestion_id}", description=str(self.content))
        embed.add_field(name="Author", value=interaction.user.mention, inline=True)
        embed.add_field(name="Status", value="🟡 Open", inline=True)
        embed.add_field(name="Votes", value="👍 0  •  👎 0", inline=False)
        message = await channel.send(embed=embed, view=SuggestionView(self.bot))
        await self.bot.database.execute(
            "UPDATE suggestions SET message_id = ? WHERE id = ?", (message.id, suggestion_id)
        )
        await interaction.response.send_message(
            embed=EmbedFactory.success(title="Suggestion submitted", description=f"Posted in {channel.mention}."),
            ephemeral=True,
        )
