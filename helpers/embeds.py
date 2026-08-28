from __future__ import annotations

from datetime import UTC, datetime
from enum import IntEnum

import discord

from config import settings


class EmbedColor(IntEnum):
    PRIMARY = 0x5865F2
    SUCCESS = 0x57F287
    WARNING = 0xFEE75C
    ERROR = 0xED4245
    INFO = 0x3498DB
    TICKET = 0x9B59B6
    MODERATION = 0xE67E22
    SYSTEM = 0x2ECC71
    NEUTRAL = 0x2B2D31


class EmbedFactory:
    @staticmethod
    def base(
        *,
        title: str | None = None,
        description: str | None = None,
        color: int | discord.Color | None = None,
        timestamp: bool = True,
    ) -> discord.Embed:
        embed = discord.Embed(
            title=title,
            description=description,
            color=color if color is not None else settings.default_embed_color,
            timestamp=datetime.now(UTC) if timestamp else None,
        )
        embed.set_footer(text=f"{settings.bot_name} • Community Management")
        return embed

    @classmethod
    def info(cls, *, title: str, description: str | None = None) -> discord.Embed:
        return cls.base(title=f"ℹ️  {title}", description=description, color=EmbedColor.INFO)

    @classmethod
    def success(cls, *, title: str, description: str | None = None) -> discord.Embed:
        return cls.base(title=f"✓  {title}", description=description, color=EmbedColor.SUCCESS)

    @classmethod
    def warning(cls, *, title: str, description: str | None = None) -> discord.Embed:
        return cls.base(title=f"⚠️  {title}", description=description, color=EmbedColor.WARNING)

    @classmethod
    def error(cls, *, title: str, description: str | None = None) -> discord.Embed:
        return cls.base(title=f"✕  {title}", description=description, color=EmbedColor.ERROR)

    @classmethod
    def ticket(cls, *, title: str, description: str | None = None) -> discord.Embed:
        return cls.base(title=f"🎫  {title}", description=description, color=EmbedColor.TICKET)

    @classmethod
    def moderation(cls, *, title: str, description: str | None = None) -> discord.Embed:
        return cls.base(title=f"🛡️  {title}", description=description, color=EmbedColor.MODERATION)

    @classmethod
    def system(cls, *, title: str, description: str | None = None) -> discord.Embed:
        return cls.base(title=f"🖥️  {title}", description=description, color=EmbedColor.SYSTEM)
