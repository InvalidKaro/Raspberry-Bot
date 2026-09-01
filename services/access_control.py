from __future__ import annotations
from enum import IntEnum
import discord

class AccessLevel(IntEnum):
    MEMBER = 0
    TICKETSTAFF = 10
    PERSO = 20
    MODERATOR = 30
    ADMIN = 40
    OWNER = 100

NAMES = {
    "member": AccessLevel.MEMBER,
    "ticketstaff": AccessLevel.TICKETSTAFF,
    "perso": AccessLevel.PERSO,
    "moderator": AccessLevel.MODERATOR,
    "admin": AccessLevel.ADMIN,
    "owner": AccessLevel.OWNER,
}

class AccessControl:
    def __init__(self, bot) -> None:
        self.bot = bot

    async def level_for(self, member: discord.Member) -> AccessLevel:
        if member.id in self.bot.settings.owner_ids:
            return AccessLevel.OWNER
        if member.guild.owner_id == member.id:
            return AccessLevel.ADMIN
        if member.guild_permissions.administrator:
            return AccessLevel.ADMIN
        rows = await self.bot.database.fetchall(
            "SELECT role_id, level FROM bot_access_roles WHERE guild_id = ?",
            (member.guild.id,),
        )
        role_ids = {r.id for r in member.roles}
        best = AccessLevel.MEMBER
        for row in rows:
            if int(row["role_id"]) in role_ids:
                best = max(best, NAMES.get(str(row["level"]).lower(), AccessLevel.MEMBER))
        return best

    async def has(self, member: discord.Member, minimum: AccessLevel) -> bool:
        return await self.level_for(member) >= minimum
