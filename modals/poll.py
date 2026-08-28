from __future__ import annotations

import json

import discord

from helpers.embeds import EmbedFactory
from views.polls import PollView


class PollModal(discord.ui.Modal, title="Create Poll"):
    question = discord.ui.TextInput(label="Question", max_length=200)
    option_1 = discord.ui.TextInput(label="Option 1", max_length=80)
    option_2 = discord.ui.TextInput(label="Option 2", max_length=80)
    option_3 = discord.ui.TextInput(label="Option 3 (optional)", required=False, max_length=80)
    option_4 = discord.ui.TextInput(label="Option 4 (optional)", required=False, max_length=80)

    def __init__(self, bot: object) -> None:
        super().__init__(timeout=300)
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not isinstance(interaction.channel, discord.TextChannel) or interaction.guild_id is None:
            return
        options = [str(self.option_1), str(self.option_2)]
        options.extend(value for value in (str(self.option_3), str(self.option_4)) if value.strip())
        poll_id = await self.bot.database.execute(
            "INSERT INTO polls (guild_id, channel_id, author_id, question, options_json) VALUES (?, ?, ?, ?, ?)",
            (interaction.guild_id, interaction.channel.id, interaction.user.id, str(self.question), json.dumps(options, ensure_ascii=False)),
        )
        embed = EmbedFactory.info(title=f"Poll #{poll_id}", description=str(self.question))
        for index, option in enumerate(options, 1):
            embed.add_field(name=f"{index}. {option}", value="0 votes", inline=False)
        message = await interaction.channel.send(embed=embed, view=PollView(self.bot, options))
        await self.bot.database.execute("UPDATE polls SET message_id = ? WHERE id = ?", (message.id, poll_id))
        await interaction.response.send_message(embed=EmbedFactory.success(title="Poll created", description=message.jump_url), ephemeral=True)
