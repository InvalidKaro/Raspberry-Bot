from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from helpers.embeds import EmbedFactory


def _walk_commands(bot: commands.Bot) -> list[app_commands.Command | app_commands.Group]:
    result: list[app_commands.Command | app_commands.Group] = []
    for root in bot.tree.get_commands():
        result.append(root)
        if isinstance(root, app_commands.Group):
            result.extend(root.walk_commands())
    return result


def _related_commands(
    bot: commands.Bot,
    command_name: str,
) -> list[app_commands.Command | app_commands.Group]:
    wanted = command_name.strip().lstrip("/").lower()
    commands_list = _walk_commands(bot)
    current = next((cmd for cmd in commands_list if cmd.qualified_name.lower() == wanted), None)

    if current is None:
        root_name = wanted.split(" ", 1)[0]
        return [
            cmd
            for cmd in commands_list
            if cmd.qualified_name.lower().startswith(root_name + " ")
        ][:8]

    parent = getattr(current, "parent", None)
    if isinstance(parent, app_commands.Group):
        candidates = [cmd for cmd in parent.commands if cmd is not current]
    else:
        root_name = current.qualified_name.split(" ", 1)[0]
        candidates = [
            cmd
            for cmd in commands_list
            if cmd is not current and cmd.qualified_name.split(" ", 1)[0] == root_name
        ]

    return candidates[:8]


def related_embed(bot: commands.Bot, command_name: str) -> discord.Embed:
    related = _related_commands(bot, command_name)
    if related:
        lines = [
            f"`/{cmd.qualified_name}` — {cmd.description or 'Keine Beschreibung.'}"
            for cmd in related
        ]
        description = "\n".join(lines)
    else:
        description = "Für diesen Command wurden keine direkten verwandten Commands gefunden."

    return EmbedFactory.info(
        title=f"Verwandte Aktionen · /{command_name}",
        description=description,
    )


class CommandActionsView(discord.ui.View):
    """Small contextual action bar appended to ordinary command responses."""

    def __init__(self, bot: commands.Bot, user_id: int, command_name: str) -> None:
        super().__init__(timeout=300)
        self.bot = bot
        self.user_id = int(user_id)
        self.command_name = command_name.strip().lstrip("/") or "unknown"

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message(
            "Diese Action Suggestions gehören zur ursprünglichen Command-Session.",
            ephemeral=True,
        )
        return False

    @discord.ui.button(
        label="Verwandte Commands",
        emoji="💡",
        style=discord.ButtonStyle.secondary,
        row=0,
    )
    async def related(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_message(
            embed=related_embed(self.bot, self.command_name),
            ephemeral=True,
        )

    @discord.ui.button(
        label="Control Center",
        emoji="🏠",
        style=discord.ButtonStyle.primary,
        row=0,
    )
    async def control_center(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        from views.control_center import ControlCenterView, control_center_embed

        await interaction.response.send_message(
            embed=control_center_embed(),
            view=ControlCenterView(self.bot, interaction.user.id),
            ephemeral=True,
        )


class ErrorActionsView(CommandActionsView):
    """Context actions for failed commands with an optional diagnostic shortcut."""

    def __init__(self, bot: commands.Bot, user_id: int, command_name: str) -> None:
        super().__init__(bot, user_id, command_name)

    @discord.ui.button(
        label="Diagnose",
        emoji="🔬",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def diagnose(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not isinstance(interaction.user, discord.Member) or not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message(
                "Für die Diagnose wird **Server verwalten** benötigt.",
                ephemeral=True,
            )
            return

        from views.control_center import run_diagnostics

        await interaction.response.send_message(
            embed=EmbedFactory.system(
                title="Diagnose wird vorbereitet",
                description="Die Prüfungen werden gestartet…",
            ),
            ephemeral=True,
        )
        await run_diagnostics(interaction, self.bot, interaction.user.id)
