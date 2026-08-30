from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import discord
from discord import app_commands
from discord.ext import commands, tasks

from helpers.embeds import EmbedFactory


_DURATION_RE = re.compile(r"(?P<value>\d+)(?P<unit>[smhdw])", re.IGNORECASE)
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def parse_duration(value: str) -> int:
    raw = value.strip().lower().replace(" ", "")
    if not raw:
        raise ValueError("Enter a duration such as `15m`, `2h`, `1d` or `1h30m`.")

    total = 0
    pos = 0
    for match in _DURATION_RE.finditer(raw):
        if match.start() != pos:
            raise ValueError("Invalid duration. Examples: `15m`, `2h`, `1d`, `1h30m`.")
        total += int(match.group("value")) * _UNIT_SECONDS[match.group("unit")]
        pos = match.end()
    if pos != len(raw) or total <= 0:
        raise ValueError("Invalid duration. Examples: `15m`, `2h`, `1d`, `1h30m`.")
    if total > 180 * 86400:
        raise ValueError("Reminders can be scheduled up to 180 days ahead.")
    return total


class Reminders(commands.GroupCog, group_name="reminder", group_description="Persistent personal reminders"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.delivery_loop.start()

    def cog_unload(self) -> None:
        self.delivery_loop.cancel()

    @tasks.loop(seconds=30)
    async def delivery_loop(self) -> None:
        rows = await self.bot.database.fetchall(
            "SELECT id, guild_id, channel_id, user_id, message, due_at "
            "FROM reminders WHERE delivered = 0 AND due_at <= CURRENT_TIMESTAMP "
            "ORDER BY due_at ASC LIMIT 25"
        )
        for row in rows:
            reminder_id = int(row["id"])
            user_id = int(row["user_id"])
            text = str(row["message"])
            delivered = False

            channel = self.bot.get_channel(int(row["channel_id"])) if row["channel_id"] else None
            if isinstance(channel, discord.abc.Messageable):
                try:
                    await channel.send(
                        content=f"<@{user_id}>",
                        embed=EmbedFactory.info(title=f"Reminder #{reminder_id}", description=text[:4000]),
                        allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
                    )
                    delivered = True
                except discord.HTTPException:
                    pass

            if not delivered:
                try:
                    user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
                    await user.send(embed=EmbedFactory.info(title=f"Reminder #{reminder_id}", description=text[:4000]))
                    delivered = True
                except (discord.HTTPException, discord.NotFound):
                    pass

            if delivered:
                await self.bot.database.execute(
                    "UPDATE reminders SET delivered = 1, delivered_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (reminder_id,),
                )

    @delivery_loop.before_loop
    async def before_delivery_loop(self) -> None:
        await self.bot.wait_until_ready()

    @app_commands.command(name="create", description="Create a reminder, e.g. 15m, 2h, 1d or 1h30m.")
    async def create(self, interaction: discord.Interaction, in_time: str, message: str) -> None:
        try:
            seconds = parse_duration(in_time)
        except ValueError as exc:
            await interaction.response.send_message(
                embed=EmbedFactory.error(title="Invalid duration", description=str(exc)),
                ephemeral=True,
            )
            return

        due = datetime.now(UTC) + timedelta(seconds=seconds)
        reminder_id = await self.bot.database.execute(
            "INSERT INTO reminders (guild_id, channel_id, user_id, message, due_at) VALUES (?, ?, ?, ?, ?)",
            (
                interaction.guild_id,
                interaction.channel_id,
                interaction.user.id,
                str(message),
                due.strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )
        timestamp = int(due.timestamp())
        await interaction.response.send_message(
            embed=EmbedFactory.success(
                title=f"Reminder #{reminder_id} created",
                description=f"I will remind you <t:{timestamp}:R>.\n\n{str(message)[:2500]}",
            ),
            ephemeral=True,
        )

    @app_commands.command(name="list", description="List your pending reminders.")
    async def list_reminders(self, interaction: discord.Interaction) -> None:
        rows = await self.bot.database.fetchall(
            "SELECT id, message, due_at FROM reminders WHERE user_id = ? AND delivered = 0 ORDER BY due_at ASC LIMIT 20",
            (interaction.user.id,),
        )
        if not rows:
            await interaction.response.send_message(
                embed=EmbedFactory.info(title="Reminders", description="You have no pending reminders."),
                ephemeral=True,
            )
            return

        lines: list[str] = []
        for row in rows:
            try:
                due = datetime.strptime(str(row["due_at"]), "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
                when = f"<t:{int(due.timestamp())}:R>"
            except ValueError:
                when = str(row["due_at"])
            msg = str(row["message"]).replace("\n", " ")[:90]
            lines.append(f"`#{row['id']}` • {when} • {msg}")

        await interaction.response.send_message(
            embed=EmbedFactory.info(title="Pending Reminders", description="\n".join(lines)),
            ephemeral=True,
        )

    @app_commands.command(name="cancel", description="Cancel one of your pending reminders.")
    async def cancel(self, interaction: discord.Interaction, reminder_id: int) -> None:
        row = await self.bot.database.fetchone(
            "SELECT id FROM reminders WHERE id = ? AND user_id = ? AND delivered = 0",
            (reminder_id, interaction.user.id),
        )
        if row is None:
            await interaction.response.send_message(
                embed=EmbedFactory.error(title="Reminder not found", description="No pending reminder with that ID belongs to you."),
                ephemeral=True,
            )
            return
        await self.bot.database.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
        await interaction.response.send_message(
            embed=EmbedFactory.success(title="Reminder cancelled", description=f"Reminder `#{reminder_id}` was removed."),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Reminders(bot))
