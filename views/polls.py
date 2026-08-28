from __future__ import annotations

import json

import discord

from helpers.embeds import EmbedFactory


class PollVoteButton(discord.ui.Button):
    def __init__(self, bot: object, index: int, label: str) -> None:
        super().__init__(
            label=f"{index + 1}",
            style=discord.ButtonStyle.secondary,
            custom_id=f"raspberry_bot:poll:vote:{index}",
        )
        self.bot = bot
        self.index = index
        self.option_label = label

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.message is None:
            return
        poll = await self.bot.database.fetchone(
            "SELECT * FROM polls WHERE message_id = ?",
            (interaction.message.id,),
        )
        if poll is None:
            await interaction.response.send_message("Poll record not found.", ephemeral=True)
            return
        options = json.loads(str(poll["options_json"]))
        if self.index >= len(options):
            await interaction.response.send_message("This poll option is no longer available.", ephemeral=True)
            return
        poll_id = int(poll["id"])
        await self.bot.database.execute(
            "INSERT INTO poll_votes (poll_id, user_id, option_index) VALUES (?, ?, ?) "
            "ON CONFLICT(poll_id, user_id) DO UPDATE SET option_index = excluded.option_index, created_at = CURRENT_TIMESTAMP",
            (poll_id, interaction.user.id, self.index),
        )
        await interaction.response.send_message(f"Vote recorded for **{options[self.index]}**.", ephemeral=True)
        rows = await self.bot.database.fetchall(
            "SELECT option_index, COUNT(*) AS count FROM poll_votes WHERE poll_id = ? GROUP BY option_index",
            (poll_id,),
        )
        counts = {int(row["option_index"]): int(row["count"]) for row in rows}
        embed = EmbedFactory.info(title=f"Poll #{poll_id}", description=str(poll["question"]))
        for index, option in enumerate(options):
            embed.add_field(name=f"{index + 1}. {option}", value=f"**{counts.get(index, 0)}** votes", inline=False)
        await interaction.message.edit(embed=embed, view=self.view)


class PollView(discord.ui.View):
    def __init__(self, bot: object, options: list[str]) -> None:
        super().__init__(timeout=None)
        for index, option in enumerate(options[:4]):
            self.add_item(PollVoteButton(bot, index, option))
