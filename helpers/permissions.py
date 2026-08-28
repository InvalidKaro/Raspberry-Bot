from __future__ import annotations

import discord


async def is_ticket_staff(bot: object, member: discord.Member) -> bool:
    if member.guild_permissions.administrator or member.guild.owner_id == member.id:
        return True

    settings_repo = getattr(bot, "settings_repo", None)
    if settings_repo is not None:
        allowed_role_ids = set(await settings_repo.list_ticket_staff_roles(member.guild.id))
    else:
        database = getattr(bot, "database", None)
        if database is None:
            return False
        rows = await database.fetchall(
            "SELECT role_id FROM ticket_staff_roles WHERE guild_id = ?",
            (member.guild.id,),
        )
        allowed_role_ids = {int(row["role_id"]) for row in rows}

    return bool({role.id for role in member.roles} & allowed_role_ids)


def is_bot_owner_id(bot: object, user_id: int) -> bool:
    settings = getattr(bot, "settings", None)
    return bool(settings and user_id in settings.owner_ids)
