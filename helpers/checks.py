from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

import discord
from discord import app_commands

from helpers.permissions import is_ticket_staff


def ticket_staff_only() -> Callable[[Callable[..., Coroutine[Any, Any, Any]]], Callable[..., Coroutine[Any, Any, Any]]]:
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return False
        return await is_ticket_staff(interaction.client, interaction.user)

    return app_commands.check(predicate)
