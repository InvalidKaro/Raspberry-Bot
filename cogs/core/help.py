from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import discord
from discord import app_commands
from discord.ext import commands

from helpers.embeds import EmbedFactory, EmbedColor


# ---------------------------------------------------------------------------
# Help structure
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class HelpCategory:
    key: str
    label: str
    emoji: str
    description: str
    prefixes: tuple[str, ...]


HELP_CATEGORIES: tuple[HelpCategory, ...] = (
    HelpCategory(
        "core",
        "Core & Utilities",
        "🏠",
        "General information, profiles, server utilities and everyday commands.",
        (
            "ping", "status", "botinfo", "userinfo", "serverinfo", "avatar",
            "roleinfo", "channelinfo", "permissions", "commandinfo", "timestamp",
            "snowflake", "membercount", "servericon", "invite", "help",
        ),
    ),
    HelpCategory(
        "tickets",
        "Tickets",
        "🎫",
        "Ticket panels, staff workflow, transcripts and ticket management.",
        ("ticket",),
    ),
    HelpCategory(
        "moderation",
        "Moderation",
        "🛡️",
        "Moderation actions, cases, warnings and channel controls.",
        ("mod",),
    ),
    HelpCategory(
        "community",
        "Community",
        "💬",
        "Polls, suggestions, reminders, onboarding and community features.",
        ("suggest", "poll", "reminder"),
    ),
    HelpCategory(
        "personnel",
        "MD Personnel",
        "📊",
        "Personnel statistics and presentation-ready graphs for the MD personnel department.",
        ("perso",),
    ),
    HelpCategory(
        "system",
        "Raspberry Pi & System",
        "🖥️",
        "Live system metrics, Pi-hole, health information and Raspberry Pi monitoring.",
        ("system",),
    ),
    HelpCategory(
        "management",
        "Server Management",
        "🔧",
        "Server setup, roles, announcements, welcome messages and configuration.",
        ("manage", "setup"),
    ),
    HelpCategory(
        "logs",
        "Bot Logs",
        "📜",
        "Owner-controlled Discord forwarding for the Raspberry-Bot runtime log.",
        ("botlog",),
    ),
    HelpCategory(
        "owner",
        "Bot Owner / Developer",
        "🔐",
        "Diagnostics, extensions, database tools, cache tools and developer operations.",
        ("dev",),
    ),
)

CATEGORY_BY_KEY = {category.key: category for category in HELP_CATEGORIES}

COMMANDS_PER_PAGE = 7
VIEW_TIMEOUT = 300.0


# ---------------------------------------------------------------------------
# Command discovery / documentation
# ---------------------------------------------------------------------------

def _qualified_name(command: app_commands.Command | app_commands.Group) -> str:
    name = getattr(command, "qualified_name", None)
    if name:
        return str(name)
    return str(command.name)


def _walk_leaf_commands(
    commands_to_walk: Iterable[app_commands.Command | app_commands.Group],
) -> list[app_commands.Command]:
    result: list[app_commands.Command] = []

    for command in commands_to_walk:
        if isinstance(command, app_commands.Group):
            result.extend(_walk_leaf_commands(command.commands))
        else:
            result.append(command)

    return result


def _all_commands(bot: commands.Bot) -> list[app_commands.Command]:
    commands_found = _walk_leaf_commands(bot.tree.get_commands())
    return sorted(commands_found, key=lambda command: _qualified_name(command).lower())


def _root_name(command: app_commands.Command) -> str:
    return _qualified_name(command).split(" ", 1)[0].lower()


def _category_commands(bot: commands.Bot, category: HelpCategory) -> list[app_commands.Command]:
    prefixes = set(category.prefixes)
    return [
        command
        for command in _all_commands(bot)
        if _root_name(command) in prefixes
    ]


def _find_command(bot: commands.Bot, query: str) -> app_commands.Command | None:
    normalized = query.strip().lower().lstrip("/")

    for command in _all_commands(bot):
        if _qualified_name(command).lower() == normalized:
            return command

    return None


def _safe_description(command: app_commands.Command) -> str:
    description = (getattr(command, "description", "") or "").strip()
    if not description or description == "…":
        return "No additional description available."
    return description


def _parameter_usage(command: app_commands.Command) -> str:
    parameters = getattr(command, "parameters", None) or []
    parts: list[str] = []

    for parameter in parameters:
        name = getattr(parameter, "display_name", None) or getattr(parameter, "name", "value")
        required = bool(getattr(parameter, "required", False))

        if required:
            parts.append(f"<{name}>")
        else:
            parts.append(f"[{name}]")

    suffix = f" {' '.join(parts)}" if parts else ""
    return f"/{_qualified_name(command)}{suffix}"


def _parameter_lines(command: app_commands.Command) -> list[str]:
    parameters = getattr(command, "parameters", None) or []
    lines: list[str] = []

    for parameter in parameters:
        name = getattr(parameter, "display_name", None) or getattr(parameter, "name", "value")
        required = bool(getattr(parameter, "required", False))
        description = (getattr(parameter, "description", "") or "No description.").strip()

        marker = "required" if required else "optional"
        lines.append(f"**`{name}`** · {marker}\n{description}")

    return lines


def _owner_only_category(category: HelpCategory) -> bool:
    return category.key in {"owner", "logs"}


def _is_owner(bot: commands.Bot, user_id: int) -> bool:
    settings = getattr(bot, "settings", None)
    owner_ids = getattr(settings, "owner_ids", set())
    return user_id in owner_ids


# ---------------------------------------------------------------------------
# Embed builders
# ---------------------------------------------------------------------------

def build_home_embed(
    bot: commands.Bot,
    *,
    user: discord.abc.User,
    visible_categories: tuple[HelpCategory, ...],
) -> discord.Embed:
    total_commands = len(_all_commands(bot))

    embed = EmbedFactory.base(
        title="📚  Raspberry-Bot Documentation",
        description=(
            "Interactive command documentation for Raspberry-Bot.\n\n"
            "Use the **category menu** or the navigation buttons below. "
            "You can also run `/help command:<command>` to open the detailed documentation "
            "for one command directly."
        ),
        color=EmbedColor.PRIMARY,
    )

    embed.add_field(
        name="Overview",
        value=(
            f"**{total_commands}** application commands discovered\n"
            f"**{len(visible_categories)}** documentation categories\n"
            "Interactive pages · command details · parameter reference"
        ),
        inline=False,
    )

    category_lines = []
    for category in visible_categories:
        count = len(_category_commands(bot, category))
        category_lines.append(
            f"{category.emoji} **{category.label}** · `{count}` command{'s' if count != 1 else ''}\n"
            f"└ {category.description}"
        )

    embed.add_field(
        name="Categories",
        value="\n\n".join(category_lines) or "No categories available.",
        inline=False,
    )

    embed.add_field(
        name="Quick usage",
        value=(
            "`/help` — open this documentation\n"
            "`/help command:perso weekly` — open one command directly\n"
            "`/commandinfo` — technical information about a command"
        ),
        inline=False,
    )

    avatar = getattr(user, "display_avatar", None)
    if avatar:
        embed.set_author(name=f"Documentation opened by {user}", icon_url=avatar.url)

    if bot.user and bot.user.display_avatar:
        embed.set_thumbnail(url=bot.user.display_avatar.url)

    embed.set_footer(
        text="Raspberry-Bot • Documentation Home • Use the controls below"
    )
    return embed


def build_category_embed(
    bot: commands.Bot,
    category: HelpCategory,
    *,
    page: int,
    user: discord.abc.User,
) -> discord.Embed:
    commands_found = _category_commands(bot, category)
    total_pages = max(1, (len(commands_found) + COMMANDS_PER_PAGE - 1) // COMMANDS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))

    start = page * COMMANDS_PER_PAGE
    end = start + COMMANDS_PER_PAGE
    page_commands = commands_found[start:end]

    embed = EmbedFactory.base(
        title=f"{category.emoji}  {category.label}",
        description=(
            f"{category.description}\n\n"
            "Select a command below or use `/help command:<name>` for its complete reference."
        ),
        color=EmbedColor.PRIMARY,
    )

    if not page_commands:
        embed.add_field(
            name="No commands found",
            value=(
                "This category currently has no registered application commands. "
                "If you recently added commands, run the bot's command sync."
            ),
            inline=False,
        )
    else:
        for command in page_commands:
            embed.add_field(
                name=f"/{_qualified_name(command)}",
                value=_safe_description(command),
                inline=False,
            )

    embed.add_field(
        name="Legend",
        value="`<parameter>` required · `[parameter]` optional",
        inline=False,
    )

    avatar = getattr(user, "display_avatar", None)
    if avatar:
        embed.set_author(name=f"Raspberry-Bot Docs • {user}", icon_url=avatar.url)

    embed.set_footer(
        text=(
            f"{category.label} • Page {page + 1}/{total_pages} "
            f"• {len(commands_found)} command{'s' if len(commands_found) != 1 else ''}"
        )
    )
    return embed


def build_command_embed(
    command: app_commands.Command,
    *,
    user: discord.abc.User,
) -> discord.Embed:
    name = _qualified_name(command)
    description = _safe_description(command)

    embed = EmbedFactory.base(
        title=f"📖  /{name}",
        description=description,
        color=EmbedColor.INFO,
    )

    embed.add_field(
        name="Usage",
        value=f"```text\n{_parameter_usage(command)}\n```",
        inline=False,
    )

    parameter_lines = _parameter_lines(command)
    if parameter_lines:
        # Discord fields are capped, so split the parameter reference when needed.
        chunks: list[str] = []
        current = ""

        for line in parameter_lines:
            candidate = line if not current else f"{current}\n\n{line}"
            if len(candidate) > 950:
                chunks.append(current)
                current = line
            else:
                current = candidate

        if current:
            chunks.append(current)

        for index, chunk in enumerate(chunks, start=1):
            title = "Parameters" if len(chunks) == 1 else f"Parameters · {index}/{len(chunks)}"
            embed.add_field(name=title, value=chunk, inline=False)
    else:
        embed.add_field(
            name="Parameters",
            value="This command has no parameters.",
            inline=False,
        )

    parent = getattr(command, "parent", None)
    if parent:
        embed.add_field(
            name="Command group",
            value=f"`/{getattr(parent, 'qualified_name', parent.name)}`",
            inline=True,
        )

    embed.add_field(
        name="Availability",
        value=(
            "Server permissions, bot-owner restrictions or server configuration may apply "
            "depending on the command."
        ),
        inline=False,
    )

    avatar = getattr(user, "display_avatar", None)
    if avatar:
        embed.set_author(name=f"Command reference • {user}", icon_url=avatar.url)

    embed.set_footer(
        text="Raspberry-Bot • Command Documentation • Back returns to the category"
    )
    return embed


# ---------------------------------------------------------------------------
# UI components
# ---------------------------------------------------------------------------

class CategorySelect(discord.ui.Select):
    def __init__(self, view: "HelpView") -> None:
        options = [
            discord.SelectOption(
                label=category.label,
                value=category.key,
                emoji=category.emoji,
                description=category.description[:100],
                default=(view.category_key == category.key),
            )
            for category in view.visible_categories
        ]

        super().__init__(
            placeholder="Browse documentation categories…",
            min_values=1,
            max_values=1,
            options=options,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        assert isinstance(self.view, HelpView)
        self.view.category_key = self.values[0]
        self.view.page = 0
        self.view.command_name = None
        self.view.rebuild_components()

        category = CATEGORY_BY_KEY[self.view.category_key]
        await interaction.response.edit_message(
            embed=build_category_embed(
                self.view.bot,
                category,
                page=self.view.page,
                user=interaction.user,
            ),
            view=self.view,
        )


class CommandSelect(discord.ui.Select):
    def __init__(self, view: "HelpView") -> None:
        category = CATEGORY_BY_KEY[view.category_key]
        commands_found = _category_commands(view.bot, category)

        start = view.page * COMMANDS_PER_PAGE
        page_commands = commands_found[start:start + COMMANDS_PER_PAGE]

        options = [
            discord.SelectOption(
                label=f"/{_qualified_name(command)}"[:100],
                value=_qualified_name(command),
                description=_safe_description(command)[:100],
            )
            for command in page_commands
        ]

        if not options:
            options = [
                discord.SelectOption(
                    label="No commands on this page",
                    value="__none__",
                    description="Choose another category.",
                )
            ]

        super().__init__(
            placeholder="Open command documentation…",
            min_values=1,
            max_values=1,
            options=options,
            disabled=page_commands == [],
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        assert isinstance(self.view, HelpView)

        if self.values[0] == "__none__":
            await interaction.response.defer()
            return

        command = _find_command(self.view.bot, self.values[0])
        if command is None:
            await interaction.response.send_message(
                "That command is no longer registered. Re-open `/help` after syncing commands.",
                ephemeral=True,
            )
            return

        self.view.command_name = _qualified_name(command)
        self.view.rebuild_components()

        await interaction.response.edit_message(
            embed=build_command_embed(command, user=interaction.user),
            view=self.view,
        )


class HelpView(discord.ui.View):
    def __init__(
        self,
        bot: commands.Bot,
        author_id: int,
        *,
        initial_category: str | None = None,
        initial_command: str | None = None,
    ) -> None:
        super().__init__(timeout=VIEW_TIMEOUT)
        self.bot = bot
        self.author_id = author_id
        self.page = 0
        self.category_key = initial_category
        self.command_name = initial_command
        self.message: discord.Message | None = None

        owner = _is_owner(bot, author_id)
        self.visible_categories = tuple(
            category
            for category in HELP_CATEGORIES
            if owner or not _owner_only_category(category)
        )

        self.rebuild_components()

    def current_category(self) -> HelpCategory | None:
        if self.category_key is None:
            return None
        return CATEGORY_BY_KEY.get(self.category_key)

    def current_total_pages(self) -> int:
        category = self.current_category()
        if category is None:
            return 1
        count = len(_category_commands(self.bot, category))
        return max(1, (count + COMMANDS_PER_PAGE - 1) // COMMANDS_PER_PAGE)

    def rebuild_components(self) -> None:
        self.clear_items()

        self.add_item(CategorySelect(self))

        if self.category_key is not None:
            self.add_item(CommandSelect(self))

        self.previous_page.disabled = (
            self.category_key is None
            or self.command_name is not None
            or self.page <= 0
        )
        self.next_page.disabled = (
            self.category_key is None
            or self.command_name is not None
            or self.page >= self.current_total_pages() - 1
        )
        self.back.disabled = self.category_key is None and self.command_name is None
        self.home.disabled = self.category_key is None and self.command_name is None

        self.add_item(self.previous_page)
        self.add_item(self.home)
        self.add_item(self.back)
        self.add_item(self.next_page)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.author_id:
            return True

        await interaction.response.send_message(
            "This documentation session belongs to another user. Run `/help` to open your own.",
            ephemeral=True,
        )
        return False

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True

        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    @discord.ui.button(label="Previous", emoji="◀️", style=discord.ButtonStyle.secondary, row=2)
    async def previous_page(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        category = self.current_category()
        if category is None:
            await interaction.response.defer()
            return

        self.page = max(0, self.page - 1)
        self.command_name = None
        self.rebuild_components()

        await interaction.response.edit_message(
            embed=build_category_embed(
                self.bot,
                category,
                page=self.page,
                user=interaction.user,
            ),
            view=self,
        )

    @discord.ui.button(label="Home", emoji="🏠", style=discord.ButtonStyle.primary, row=2)
    async def home(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        self.category_key = None
        self.command_name = None
        self.page = 0
        self.rebuild_components()

        await interaction.response.edit_message(
            embed=build_home_embed(
                self.bot,
                user=interaction.user,
                visible_categories=self.visible_categories,
            ),
            view=self,
        )

    @discord.ui.button(label="Back", emoji="↩️", style=discord.ButtonStyle.secondary, row=2)
    async def back(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if self.command_name is not None:
            self.command_name = None
            category = self.current_category()

            if category is None:
                self.rebuild_components()
                await interaction.response.edit_message(
                    embed=build_home_embed(
                        self.bot,
                        user=interaction.user,
                        visible_categories=self.visible_categories,
                    ),
                    view=self,
                )
                return

            self.rebuild_components()
            await interaction.response.edit_message(
                embed=build_category_embed(
                    self.bot,
                    category,
                    page=self.page,
                    user=interaction.user,
                ),
                view=self,
            )
            return

        self.category_key = None
        self.command_name = None
        self.page = 0
        self.rebuild_components()

        await interaction.response.edit_message(
            embed=build_home_embed(
                self.bot,
                user=interaction.user,
                visible_categories=self.visible_categories,
            ),
            view=self,
        )

    @discord.ui.button(label="Next", emoji="▶️", style=discord.ButtonStyle.secondary, row=2)
    async def next_page(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        category = self.current_category()
        if category is None:
            await interaction.response.defer()
            return

        self.page = min(self.current_total_pages() - 1, self.page + 1)
        self.command_name = None
        self.rebuild_components()

        await interaction.response.edit_message(
            embed=build_category_embed(
                self.bot,
                category,
                page=self.page,
                user=interaction.user,
            ),
            view=self,
        )


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class Help(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def command_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        needle = current.strip().lower().lstrip("/")
        owner = _is_owner(self.bot, interaction.user.id)

        hidden_roots = set()
        if not owner:
            for category in HELP_CATEGORIES:
                if _owner_only_category(category):
                    hidden_roots.update(category.prefixes)

        choices: list[app_commands.Choice[str]] = []

        for command in _all_commands(self.bot):
            name = _qualified_name(command)

            if _root_name(command) in hidden_roots:
                continue
            if needle and needle not in name.lower():
                continue

            choices.append(
                app_commands.Choice(
                    name=f"/{name}"[:100],
                    value=name[:100],
                )
            )

            if len(choices) >= 25:
                break

        return choices

    @app_commands.command(
        name="help",
        description="Open the interactive Raspberry-Bot documentation.",
    )
    @app_commands.describe(
        command="Optional command to open directly, for example 'perso weekly'."
    )
    @app_commands.autocomplete(command=command_autocomplete)
    async def help_command(
        self,
        interaction: discord.Interaction,
        command: str | None = None,
    ) -> None:
        selected_command: app_commands.Command | None = None
        selected_category: HelpCategory | None = None

        if command:
            selected_command = _find_command(self.bot, command)

            if selected_command is None:
                await interaction.response.send_message(
                    embed=EmbedFactory.error(
                        title="Command not found",
                        description=(
                            f"I could not find `/{command.lstrip('/')}`.\n"
                            "Choose a command from autocomplete or open `/help` without a parameter."
                        ),
                    ),
                    ephemeral=True,
                )
                return

            root = _root_name(selected_command)
            for category in HELP_CATEGORIES:
                if root in category.prefixes:
                    selected_category = category
                    break

            if (
                selected_category
                and _owner_only_category(selected_category)
                and not _is_owner(self.bot, interaction.user.id)
            ):
                await interaction.response.send_message(
                    embed=EmbedFactory.error(
                        title="Owner documentation",
                        description="That command is restricted to the configured bot owner.",
                    ),
                    ephemeral=True,
                )
                return

        view = HelpView(
            self.bot,
            interaction.user.id,
            initial_category=selected_category.key if selected_category else None,
            initial_command=_qualified_name(selected_command) if selected_command else None,
        )

        if selected_command:
            embed = build_command_embed(selected_command, user=interaction.user)
        elif selected_category:
            embed = build_category_embed(
                self.bot,
                selected_category,
                page=0,
                user=interaction.user,
            )
        else:
            embed = build_home_embed(
                self.bot,
                user=interaction.user,
                visible_categories=view.visible_categories,
            )

        await interaction.response.send_message(embed=embed, view=view)
        try:
            view.message = await interaction.original_response()
        except discord.HTTPException:
            pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Help(bot))
