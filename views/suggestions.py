from __future__ import annotations

import discord

from helpers.embeds import EmbedFactory


class SuggestionView(discord.ui.View):
    def __init__(self, bot: object) -> None:
        super().__init__(timeout=None)
        self.bot = bot

    async def _get(self, interaction: discord.Interaction):
        if interaction.message is None:
            return None
        return await self.bot.database.fetchone(
            "SELECT * FROM suggestions WHERE message_id = ?", (interaction.message.id,)
        )

    async def _refresh(self, interaction: discord.Interaction) -> None:
        if interaction.message is None:
            return
        row = await self._get(interaction)
        if row is None:
            return
        votes = await self.bot.database.fetchall(
            "SELECT vote, COUNT(*) AS count FROM suggestion_votes WHERE suggestion_id = ? GROUP BY vote",
            (int(row["id"]),),
        )
        counts = {int(v["vote"]): int(v["count"]) for v in votes}
        status_map = {"open": "🟡 Open", "approved": "🟢 Approved", "denied": "🔴 Denied"}
        embed = EmbedFactory.info(title=f"Suggestion #{int(row['id'])}", description=str(row["content"]))
        embed.add_field(name="Author", value=f"<@{int(row['author_id'])}>", inline=True)
        embed.add_field(name="Status", value=status_map.get(str(row["status"]), str(row["status"])), inline=True)
        embed.add_field(name="Votes", value=f"👍 {counts.get(1, 0)}  •  👎 {counts.get(-1, 0)}", inline=False)
        await interaction.message.edit(embed=embed, view=self)

    async def _vote(self, interaction: discord.Interaction, vote: int) -> None:
        row = await self._get(interaction)
        if row is None:
            await interaction.response.send_message("Suggestion record not found.", ephemeral=True)
            return
        await self.bot.database.execute(
            "INSERT INTO suggestion_votes (suggestion_id, user_id, vote) VALUES (?, ?, ?) "
            "ON CONFLICT(suggestion_id, user_id) DO UPDATE SET vote = excluded.vote, created_at = CURRENT_TIMESTAMP",
            (int(row["id"]), interaction.user.id, vote),
        )
        await interaction.response.send_message("Vote recorded.", ephemeral=True)
        await self._refresh(interaction)

    @discord.ui.button(label="Upvote", emoji="👍", style=discord.ButtonStyle.success, custom_id="raspberry_bot:suggestion:up")
    async def upvote(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._vote(interaction, 1)

    @discord.ui.button(label="Downvote", emoji="👎", style=discord.ButtonStyle.secondary, custom_id="raspberry_bot:suggestion:down")
    async def downvote(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._vote(interaction, -1)

    @discord.ui.button(label="Approve", emoji="✅", style=discord.ButtonStyle.primary, custom_id="raspberry_bot:suggestion:approve")
    async def approve(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not isinstance(interaction.user, discord.Member) or not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("Manage Server permission required.", ephemeral=True)
            return
        row = await self._get(interaction)
        if row is None:
            return
        await self.bot.database.execute("UPDATE suggestions SET status='approved' WHERE id = ?", (int(row["id"]),))
        await interaction.response.send_message(embed=EmbedFactory.success(title="Suggestion approved"), ephemeral=True)
        await self._refresh(interaction)

    @discord.ui.button(label="Deny", emoji="❌", style=discord.ButtonStyle.danger, custom_id="raspberry_bot:suggestion:deny")
    async def deny(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not isinstance(interaction.user, discord.Member) or not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("Manage Server permission required.", ephemeral=True)
            return
        row = await self._get(interaction)
        if row is None:
            return
        await self.bot.database.execute("UPDATE suggestions SET status='denied' WHERE id = ?", (int(row["id"]),))
        await interaction.response.send_message(embed=EmbedFactory.success(title="Suggestion denied"), ephemeral=True)
        await self._refresh(interaction)
