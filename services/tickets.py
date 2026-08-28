from __future__ import annotations

import asyncio
import logging
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING

import discord

from database.repositories.settings import SettingsRepository
from database.repositories.tickets import TicketRepository
from helpers.embeds import EmbedFactory
from helpers.formatting import slugify_channel_name
from helpers.permissions import is_ticket_staff
from services.transcripts import build_html_transcript

if TYPE_CHECKING:
    from bot import RaspberryBot

logger = logging.getLogger(__name__)


PRIORITY_EMOJI = {
    "low": "🟢",
    "normal": "🟡",
    "high": "🟠",
    "urgent": "🔴",
    "critical": "🟣",
}


class TicketService:
    def __init__(self, bot: RaspberryBot) -> None:
        self.bot = bot
        self.repo = TicketRepository(bot.database)
        self.settings_repo = SettingsRepository(bot.database, bot.cache)

    async def create_ticket(
        self,
        interaction: discord.Interaction,
        *,
        subject: str,
        description: str,
        category_name: str,
    ) -> discord.TextChannel:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            raise RuntimeError("Tickets can only be created inside a guild.")

        guild = interaction.guild
        opener = interaction.user
        open_count = await self.repo.count_open_for_user(guild.id, opener.id)
        if open_count >= 3:
            raise RuntimeError("You already have three open tickets.")

        settings = await self.settings_repo.get_guild_settings(guild.id)
        category_id = settings.get("ticket_category_id")
        if not category_id:
            raise RuntimeError("The ticket system has not been configured yet.")

        category = guild.get_channel(int(category_id))
        if not isinstance(category, discord.CategoryChannel):
            raise RuntimeError("The configured ticket category no longer exists.")

        ticket_id = await self.repo.create(
            guild_id=guild.id,
            opener_id=opener.id,
            subject=subject.strip()[:100],
            description=description.strip()[:2000],
            category_name=category_name.strip()[:80] or "General",
        )

        overwrites: dict[discord.Role | discord.Member, discord.PermissionOverwrite] = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            opener: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
                embed_links=True,
            ),
        }

        if guild.me is not None:
            overwrites[guild.me] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_channels=True,
                manage_messages=True,
                attach_files=True,
                embed_links=True,
            )

        for role_id in await self.settings_repo.list_ticket_staff_roles(guild.id):
            role = guild.get_role(role_id)
            if role is not None:
                overwrites[role] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    attach_files=True,
                    embed_links=True,
                )

        channel_name = f"ticket-{ticket_id:04d}-{slugify_channel_name(opener.display_name, 'user')}"
        channel = await guild.create_text_channel(
            name=channel_name[:95],
            category=category,
            overwrites=overwrites,
            topic=f"Raspberry-Bot Ticket #{ticket_id} • Owner {opener.id}",
            reason=f"Ticket #{ticket_id} created by {opener}",
        )
        await self.repo.set_channel(ticket_id, channel.id)
        await self.repo.log_event(
            ticket_id=ticket_id,
            guild_id=guild.id,
            actor_id=opener.id,
            event_type="created",
            new_value=subject,
        )

        from views.tickets.controls import TicketControlsView

        embed = self.build_ticket_embed(
            {
                "id": ticket_id,
                "opener_id": opener.id,
                "subject": subject,
                "description": description,
                "category_name": category_name,
                "priority": "normal",
                "status": "open",
                "claimed_by": None,
            },
            guild,
        )
        control_message = await channel.send(
            content=opener.mention,
            embed=embed,
            view=TicketControlsView(self.bot),
            allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
        )
        await self.repo.set_control_message(ticket_id, control_message.id)
        return channel

    def build_ticket_embed(self, ticket: dict[str, object], guild: discord.Guild) -> discord.Embed:
        priority = str(ticket.get("priority") or "normal")
        claimed_by = ticket.get("claimed_by")
        status = str(ticket.get("status") or "open")
        opener_id = int(ticket["opener_id"])
        embed = EmbedFactory.ticket(
            title=f"Ticket #{int(ticket['id']):04d}",
            description=str(ticket.get("description") or "No description provided."),
        )
        embed.add_field(name="Owner", value=f"<@{opener_id}>", inline=True)
        embed.add_field(name="Category", value=str(ticket.get("category_name") or "General"), inline=True)
        embed.add_field(
            name="Priority",
            value=f"{PRIORITY_EMOJI.get(priority, '⚪')} **{priority.title()}**",
            inline=True,
        )
        embed.add_field(name="Status", value="🟢 Open" if status == "open" else "⚫ Closed", inline=True)
        embed.add_field(name="Claimed by", value=f"<@{claimed_by}>" if claimed_by else "—", inline=True)
        embed.add_field(name="Subject", value=str(ticket.get("subject") or "—")[:1024], inline=False)
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        return embed

    async def refresh_control_message(self, channel: discord.TextChannel) -> None:
        ticket = await self.repo.get_by_channel(channel.id)
        if not ticket or not ticket.get("control_message_id"):
            return
        try:
            message = await channel.fetch_message(int(ticket["control_message_id"]))
        except discord.HTTPException:
            return
        from views.tickets.controls import TicketControlsView

        await message.edit(embed=self.build_ticket_embed(ticket, channel.guild), view=TicketControlsView(self.bot))

    async def require_ticket(self, channel: discord.abc.GuildChannel | None) -> dict[str, object]:
        if not isinstance(channel, discord.TextChannel):
            raise RuntimeError("This command must be used inside a ticket channel.")
        ticket = await self.repo.get_by_channel(channel.id)
        if ticket is None:
            raise RuntimeError("This channel is not a registered ticket.")
        return ticket

    async def require_staff(self, interaction: discord.Interaction) -> discord.Member:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            raise RuntimeError("This action is only available in servers.")
        if not await is_ticket_staff(self.bot, interaction.user):
            raise PermissionError("You are not a configured ticket staff member.")
        return interaction.user

    async def claim(self, interaction: discord.Interaction) -> str:
        member = await self.require_staff(interaction)
        ticket = await self.require_ticket(interaction.channel)
        if ticket["status"] != "open":
            raise RuntimeError("This ticket is already closed.")
        current = ticket.get("claimed_by")
        if current and int(current) != member.id:
            raise RuntimeError(f"This ticket is already claimed by <@{current}>.")
        await self.repo.set_claimed(int(ticket["id"]), member.id)
        await self.repo.log_event(
            ticket_id=int(ticket["id"]), guild_id=member.guild.id, actor_id=member.id, event_type="claimed"
        )
        if isinstance(interaction.channel, discord.TextChannel):
            await self.refresh_control_message(interaction.channel)
        return f"Ticket #{int(ticket['id']):04d} claimed by {member.mention}."

    async def unclaim(self, interaction: discord.Interaction) -> str:
        member = await self.require_staff(interaction)
        ticket = await self.require_ticket(interaction.channel)
        current = ticket.get("claimed_by")
        if not current:
            raise RuntimeError("This ticket is not claimed.")
        if int(current) != member.id and not member.guild_permissions.manage_channels:
            raise PermissionError("Only the current handler or a moderator can unclaim this ticket.")
        await self.repo.set_claimed(int(ticket["id"]), None)
        await self.repo.log_event(
            ticket_id=int(ticket["id"]), guild_id=member.guild.id, actor_id=member.id, event_type="unclaimed"
        )
        if isinstance(interaction.channel, discord.TextChannel):
            await self.refresh_control_message(interaction.channel)
        return f"Ticket #{int(ticket['id']):04d} is now unclaimed."

    async def set_priority(self, interaction: discord.Interaction, priority: str) -> str:
        member = await self.require_staff(interaction)
        ticket = await self.require_ticket(interaction.channel)
        if priority not in PRIORITY_EMOJI:
            raise ValueError("Invalid priority.")
        old = str(ticket.get("priority") or "normal")
        await self.repo.set_priority(int(ticket["id"]), priority)
        await self.repo.log_event(
            ticket_id=int(ticket["id"]), guild_id=member.guild.id, actor_id=member.id,
            event_type="priority_changed", old_value=old, new_value=priority,
        )
        if isinstance(interaction.channel, discord.TextChannel):
            await self.refresh_control_message(interaction.channel)
        return f"Priority changed from **{old.title()}** to **{priority.title()}**."

    async def add_note(self, interaction: discord.Interaction, content: str) -> str:
        member = await self.require_staff(interaction)
        ticket = await self.require_ticket(interaction.channel)
        await self.repo.add_note(int(ticket["id"]), member.id, content.strip()[:2000])
        await self.repo.log_event(
            ticket_id=int(ticket["id"]), guild_id=member.guild.id, actor_id=member.id,
            event_type="note_added",
        )
        return "Internal ticket note saved."

    async def close(self, interaction: discord.Interaction, reason: str) -> str:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            raise RuntimeError("This action is only available in servers.")
        ticket = await self.require_ticket(interaction.channel)
        if ticket["status"] == "closed":
            raise RuntimeError("This ticket is already closed.")
        actor = interaction.user
        if actor.id != int(ticket["opener_id"]) and not await is_ticket_staff(self.bot, actor):
            raise PermissionError("Only the ticket owner or ticket staff can close this ticket.")
        if not isinstance(interaction.channel, discord.TextChannel):
            raise RuntimeError("Invalid ticket channel.")

        transcript = await build_html_transcript(interaction.channel, ticket)
        transcript_bytes = transcript.getvalue()
        transcript_path = Path("data/transcripts") / str(interaction.guild.id) / f"ticket-{int(ticket['id']):04d}.html"
        await asyncio.to_thread(transcript_path.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(transcript_path.write_bytes, transcript_bytes)

        await self.repo.close(int(ticket["id"]), actor.id, reason.strip()[:500])
        await self.repo.log_event(
            ticket_id=int(ticket["id"]), guild_id=interaction.guild.id, actor_id=actor.id,
            event_type="closed", new_value=reason.strip()[:500],
        )

        settings = await self.settings_repo.get_guild_settings(interaction.guild.id)
        log_channel_id = settings.get("ticket_log_channel_id")
        log_channel = interaction.guild.get_channel(int(log_channel_id)) if log_channel_id else None
        if isinstance(log_channel, discord.TextChannel):
            log_embed = EmbedFactory.ticket(
                title=f"Ticket #{int(ticket['id']):04d} closed",
                description=f"**Subject:** {ticket.get('subject')}\n**Closed by:** {actor.mention}\n**Reason:** {reason}",
            )
            await log_channel.send(
                embed=log_embed,
                file=discord.File(BytesIO(transcript_bytes), filename=f"ticket-{int(ticket['id']):04d}.html"),
            )

        opener = interaction.guild.get_member(int(ticket["opener_id"]))
        if opener is not None:
            await interaction.channel.set_permissions(opener, view_channel=True, send_messages=False, read_message_history=True)
        for row in await self.bot.database.fetchall(
            "SELECT user_id FROM ticket_members WHERE ticket_id = ?", (int(ticket["id"]),)
        ):
            extra_member = interaction.guild.get_member(int(row["user_id"]))
            if extra_member is not None:
                await interaction.channel.set_permissions(extra_member, view_channel=True, send_messages=False)
        try:
            await interaction.channel.edit(name=f"closed-{int(ticket['id']):04d}")
        except discord.HTTPException:
            logger.warning("Could not rename closed ticket channel %s", interaction.channel.id)
        await self.refresh_control_message(interaction.channel)
        return f"Ticket #{int(ticket['id']):04d} closed. Transcript was generated."
