from __future__ import annotations

import re
from datetime import UTC

import discord


PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z0-9_.-]+)\}")

# Keep this list centralized so the event listener, preview command and help output
# always expose the exact same template language.
PLACEHOLDER_DESCRIPTIONS: tuple[tuple[str, str], ...] = (
    ("{user}", "Mention of the joining member"),
    ("{user.mention}", "Mention of the joining member"),
    ("{username}", "Discord username"),
    ("{user.name}", "Discord username"),
    ("{display_name}", "Server display name / nickname"),
    ("{user.display_name}", "Server display name / nickname"),
    ("{user.id}", "User ID"),
    ("{user.avatar}", "Avatar URL"),
    ("{user.created_at}", "Account creation date"),
    ("{user.joined_at}", "Server join date"),
    ("{user.top_role}", "Highest role after joining"),
    ("{server}", "Server name"),
    ("{server.name}", "Server name"),
    ("{server.id}", "Server ID"),
    ("{server.owner}", "Server owner mention when available"),
    ("{member_count}", "Current server member count"),
    ("{channel}", "Welcome channel mention when available"),
    ("{channel.name}", "Welcome channel name when available"),
)


def _date(value: object | None) -> str:
    if value is None:
        return "Unknown"
    try:
        # discord.py datetimes are timezone-aware; normalize for stable formatting.
        return value.astimezone(UTC).strftime("%d.%m.%Y")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        return "Unknown"


def build_welcome_values(
    member: discord.Member,
    channel: discord.abc.GuildChannel | None = None,
) -> dict[str, str]:
    guild = member.guild
    owner = guild.owner
    top_role = member.top_role
    if top_role == guild.default_role:
        top_role_text = "None"
    else:
        top_role_text = top_role.name

    channel_mention = getattr(channel, "mention", "") if channel is not None else ""
    channel_name = getattr(channel, "name", "") if channel is not None else ""

    values = {
        "user": member.mention,
        "user.mention": member.mention,
        "username": member.name,
        "user.name": member.name,
        "display_name": member.display_name,
        "user.display_name": member.display_name,
        "user.id": str(member.id),
        "user.avatar": member.display_avatar.url,
        "user.created_at": _date(member.created_at),
        "user.joined_at": _date(member.joined_at),
        "user.top_role": top_role_text,
        "server": guild.name,
        "server.name": guild.name,
        "server.id": str(guild.id),
        "server.owner": owner.mention if owner is not None else "Unknown",
        "member_count": str(guild.member_count or len(guild.members)),
        "channel": channel_mention,
        "channel.name": channel_name,
    }
    return values


def render_welcome_template(
    template: str,
    member: discord.Member,
    channel: discord.abc.GuildChannel | None = None,
    *,
    max_length: int = 4000,
) -> str:
    values = build_welcome_values(member, channel)

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        # Unknown placeholders deliberately remain untouched. This makes template
        # typos visible in preview instead of silently deleting user content.
        return values.get(key, match.group(0))

    return PLACEHOLDER_RE.sub(replace, template)[:max_length]


def placeholder_help_text() -> str:
    return "\n".join(f"`{token}` — {description}" for token, description in PLACEHOLDER_DESCRIPTIONS)
