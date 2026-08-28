from __future__ import annotations

import asyncio

import discord
from discord import app_commands
from discord.ext import commands

from database.repositories.tickets import TicketRepository
from helpers.embeds import EmbedFactory
from helpers.permissions import is_ticket_staff
from views.tickets.panel import TicketPanelView


class Tickets(commands.GroupCog, group_name="ticket", group_description="Support ticket commands"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.repo = TicketRepository(bot.database)

    @app_commands.command(name="panel", description="Send a persistent ticket creation panel.")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def panel(self, interaction: discord.Interaction, channel: discord.TextChannel | None = None) -> None:
        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            await interaction.response.send_message(
                embed=EmbedFactory.error(title="Invalid channel", description="Choose a text channel for the ticket panel."),
                ephemeral=True,
            )
            return
        embed = EmbedFactory.ticket(
            title="Support Center",
            description=(
                "Need help? Use the button below to create a private support ticket.\n\n"
                "Please describe the issue clearly. Ticket staff can claim, prioritize, add notes and close tickets with transcripts."
            ),
        )
        await target.send(embed=embed, view=TicketPanelView(self.bot))
        await interaction.response.send_message(
            embed=EmbedFactory.success(title="Ticket panel sent", description=target.mention), ephemeral=True
        )

    @app_commands.command(name="info", description="Show information about the current ticket.")
    @app_commands.guild_only()
    async def info(self, interaction: discord.Interaction) -> None:
        try:
            ticket = await self.bot.ticket_service.require_ticket(interaction.channel)
        except RuntimeError as exc:
            await interaction.response.send_message(embed=EmbedFactory.error(title="Not a ticket", description=str(exc)), ephemeral=True)
            return
        if interaction.guild is None:
            return
        await interaction.response.send_message(embed=self.bot.ticket_service.build_ticket_embed(ticket, interaction.guild))

    @app_commands.command(name="queue", description="Show the current prioritized support queue.")
    @app_commands.guild_only()
    async def queue(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return
        if not await is_ticket_staff(self.bot, interaction.user):
            await interaction.response.send_message(embed=EmbedFactory.error(title="Ticket staff only", description="You do not have ticket staff access."), ephemeral=True)
            return
        rows = await self.repo.queue(interaction.guild.id)
        if not rows:
            await interaction.response.send_message(embed=EmbedFactory.ticket(title="Support Queue", description="No open tickets."), ephemeral=True)
            return
        icons = {"low": "🟢", "normal": "🟡", "high": "🟠", "urgent": "🔴", "critical": "🟣"}
        lines = []
        for ticket in rows:
            claimed = f"<@{ticket['claimed_by']}>" if ticket.get("claimed_by") else "Unclaimed"
            lines.append(
                f"{icons.get(str(ticket['priority']), '⚪')} **#{int(ticket['id']):04d}** • <#{ticket['channel_id']}>\n"
                f"└ {ticket['subject']} • {claimed}"
            )
        await interaction.response.send_message(embed=EmbedFactory.ticket(title="Support Queue", description="\n\n".join(lines)), ephemeral=True)

    @app_commands.command(name="add", description="Add a server member to the current ticket.")
    @app_commands.guild_only()
    async def add_member(self, interaction: discord.Interaction, member: discord.Member) -> None:
        try:
            actor = await self.bot.ticket_service.require_staff(interaction)
            ticket = await self.bot.ticket_service.require_ticket(interaction.channel)
        except (RuntimeError, PermissionError) as exc:
            await interaction.response.send_message(embed=EmbedFactory.error(title="Unable to add member", description=str(exc)), ephemeral=True)
            return
        if not isinstance(interaction.channel, discord.TextChannel):
            return
        await interaction.channel.set_permissions(member, view_channel=True, send_messages=True, read_message_history=True)
        await self.repo.add_member(int(ticket["id"]), member.id, actor.id)
        await self.repo.log_event(ticket_id=int(ticket["id"]), guild_id=actor.guild.id, actor_id=actor.id, event_type="member_added", new_value=str(member.id))
        await interaction.response.send_message(embed=EmbedFactory.success(title="Member added", description=member.mention))

    @app_commands.command(name="remove", description="Remove an added member from the current ticket.")
    @app_commands.guild_only()
    async def remove_member(self, interaction: discord.Interaction, member: discord.Member) -> None:
        try:
            actor = await self.bot.ticket_service.require_staff(interaction)
            ticket = await self.bot.ticket_service.require_ticket(interaction.channel)
        except (RuntimeError, PermissionError) as exc:
            await interaction.response.send_message(embed=EmbedFactory.error(title="Unable to remove member", description=str(exc)), ephemeral=True)
            return
        if member.id == int(ticket["opener_id"]):
            await interaction.response.send_message(embed=EmbedFactory.error(title="Cannot remove owner", description="The ticket owner cannot be removed from their own ticket."), ephemeral=True)
            return
        if not isinstance(interaction.channel, discord.TextChannel):
            return
        await interaction.channel.set_permissions(member, overwrite=None)
        await self.repo.remove_member(int(ticket["id"]), member.id)
        await self.repo.log_event(ticket_id=int(ticket["id"]), guild_id=actor.guild.id, actor_id=actor.id, event_type="member_removed", old_value=str(member.id))
        await interaction.response.send_message(embed=EmbedFactory.success(title="Member removed", description=member.mention))

    @app_commands.command(name="reopen", description="Reopen a closed ticket.")
    @app_commands.guild_only()
    async def reopen(self, interaction: discord.Interaction) -> None:
        try:
            actor = await self.bot.ticket_service.require_staff(interaction)
            ticket = await self.bot.ticket_service.require_ticket(interaction.channel)
        except (RuntimeError, PermissionError) as exc:
            await interaction.response.send_message(embed=EmbedFactory.error(title="Unable to reopen", description=str(exc)), ephemeral=True)
            return
        if ticket["status"] != "closed":
            await interaction.response.send_message(embed=EmbedFactory.warning(title="Already open", description="This ticket is already open."), ephemeral=True)
            return
        await self.repo.reopen(int(ticket["id"]))
        if isinstance(interaction.channel, discord.TextChannel):
            opener = interaction.guild.get_member(int(ticket["opener_id"])) if interaction.guild else None
            if opener:
                await interaction.channel.set_permissions(opener, view_channel=True, send_messages=True, read_message_history=True)
            await interaction.channel.edit(name=f"ticket-{int(ticket['id']):04d}-reopened")
            await self.bot.ticket_service.refresh_control_message(interaction.channel)
        await self.repo.log_event(ticket_id=int(ticket["id"]), guild_id=actor.guild.id, actor_id=actor.id, event_type="reopened")
        await interaction.response.send_message(embed=EmbedFactory.success(title="Ticket reopened", description=f"Ticket #{int(ticket['id']):04d} is open again."))

    @app_commands.command(name="claim", description="Claim the current ticket.")
    @app_commands.guild_only()
    async def claim(self, interaction: discord.Interaction) -> None:
        try:
            text = await self.bot.ticket_service.claim(interaction)
        except (RuntimeError, PermissionError) as exc:
            await interaction.response.send_message(embed=EmbedFactory.error(title="Claim failed", description=str(exc)), ephemeral=True)
            return
        await interaction.response.send_message(embed=EmbedFactory.success(title="Ticket claimed", description=text))

    @app_commands.command(name="unclaim", description="Unclaim the current ticket.")
    @app_commands.guild_only()
    async def unclaim(self, interaction: discord.Interaction) -> None:
        try:
            text = await self.bot.ticket_service.unclaim(interaction)
        except (RuntimeError, PermissionError) as exc:
            await interaction.response.send_message(embed=EmbedFactory.error(title="Unclaim failed", description=str(exc)), ephemeral=True)
            return
        await interaction.response.send_message(embed=EmbedFactory.success(title="Ticket unclaimed", description=text))

    @app_commands.command(name="priority", description="Set the priority of the current ticket.")
    @app_commands.guild_only()
    @app_commands.choices(priority=[
        app_commands.Choice(name="Low", value="low"),
        app_commands.Choice(name="Normal", value="normal"),
        app_commands.Choice(name="High", value="high"),
        app_commands.Choice(name="Urgent", value="urgent"),
        app_commands.Choice(name="Critical", value="critical"),
    ])
    async def priority(self, interaction: discord.Interaction, priority: app_commands.Choice[str]) -> None:
        try:
            text = await self.bot.ticket_service.set_priority(interaction, priority.value)
        except (RuntimeError, PermissionError, ValueError) as exc:
            await interaction.response.send_message(embed=EmbedFactory.error(title="Priority update failed", description=str(exc)), ephemeral=True)
            return
        await interaction.response.send_message(embed=EmbedFactory.success(title="Priority updated", description=text))

    @app_commands.command(name="notes", description="Show recent internal notes for the current ticket.")
    @app_commands.guild_only()
    async def notes(self, interaction: discord.Interaction) -> None:
        try:
            await self.bot.ticket_service.require_staff(interaction)
            ticket = await self.bot.ticket_service.require_ticket(interaction.channel)
        except (RuntimeError, PermissionError) as exc:
            await interaction.response.send_message(embed=EmbedFactory.error(title="Unable to view notes", description=str(exc)), ephemeral=True)
            return
        notes = await self.repo.list_notes(int(ticket["id"]))
        description = "\n\n".join(
            f"**#{int(note['id'])}** • <@{int(note['author_id'])}>\n└ {str(note['content'])[:350]}"
            for note in notes[:8]
        ) or "No internal notes yet."
        await interaction.response.send_message(embed=EmbedFactory.ticket(title="Internal Notes", description=description), ephemeral=True)

    @app_commands.command(name="rename", description="Rename the current ticket channel.")
    @app_commands.guild_only()
    async def rename(self, interaction: discord.Interaction, name: str) -> None:
        try:
            actor = await self.bot.ticket_service.require_staff(interaction)
            ticket = await self.bot.ticket_service.require_ticket(interaction.channel)
        except (RuntimeError, PermissionError) as exc:
            await interaction.response.send_message(embed=EmbedFactory.error(title="Rename failed", description=str(exc)), ephemeral=True)
            return
        if not isinstance(interaction.channel, discord.TextChannel):
            return
        from helpers.formatting import slugify_channel_name
        new_name = f"ticket-{int(ticket['id']):04d}-{slugify_channel_name(name)}"[:95]
        await interaction.channel.edit(name=new_name, reason=f"Ticket renamed by {actor}")
        await self.repo.log_event(ticket_id=int(ticket["id"]), guild_id=actor.guild.id, actor_id=actor.id, event_type="renamed", new_value=new_name)
        await interaction.response.send_message(embed=EmbedFactory.success(title="Ticket renamed", description=f"New name: `#{new_name}`"))

    @app_commands.command(name="transfer", description="Transfer the current ticket claim to another ticket staff member.")
    @app_commands.guild_only()
    async def transfer(self, interaction: discord.Interaction, member: discord.Member) -> None:
        try:
            actor = await self.bot.ticket_service.require_staff(interaction)
            ticket = await self.bot.ticket_service.require_ticket(interaction.channel)
        except (RuntimeError, PermissionError) as exc:
            await interaction.response.send_message(embed=EmbedFactory.error(title="Transfer failed", description=str(exc)), ephemeral=True)
            return
        if not await is_ticket_staff(self.bot, member):
            await interaction.response.send_message(embed=EmbedFactory.error(title="Transfer failed", description="The target member is not configured as ticket staff."), ephemeral=True)
            return
        old = ticket.get("claimed_by")
        await self.repo.set_claimed(int(ticket["id"]), member.id)
        await self.repo.log_event(ticket_id=int(ticket["id"]), guild_id=actor.guild.id, actor_id=actor.id, event_type="transferred", old_value=str(old) if old else None, new_value=str(member.id))
        if isinstance(interaction.channel, discord.TextChannel):
            await self.bot.ticket_service.refresh_control_message(interaction.channel)
        await interaction.response.send_message(embed=EmbedFactory.success(title="Ticket transferred", description=f"New handler: {member.mention}"))

    @app_commands.command(name="transcript", description="Generate an HTML transcript of the current ticket.")
    @app_commands.guild_only()
    async def transcript(self, interaction: discord.Interaction) -> None:
        try:
            await self.bot.ticket_service.require_staff(interaction)
            ticket = await self.bot.ticket_service.require_ticket(interaction.channel)
        except (RuntimeError, PermissionError) as exc:
            await interaction.response.send_message(embed=EmbedFactory.error(title="Transcript unavailable", description=str(exc)), ephemeral=True)
            return
        if not isinstance(interaction.channel, discord.TextChannel):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        from services.transcripts import build_html_transcript
        transcript = await build_html_transcript(interaction.channel, ticket)
        await interaction.followup.send(
            embed=EmbedFactory.ticket(title=f"Ticket #{int(ticket['id']):04d} Transcript", description="HTML transcript generated on demand."),
            file=discord.File(transcript, filename=f"ticket-{int(ticket['id']):04d}.html"),
            ephemeral=True,
        )

    @app_commands.command(name="delete", description="Permanently delete a closed ticket channel after confirmation.")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_channels=True)
    async def delete(self, interaction: discord.Interaction) -> None:
        try:
            await self.bot.ticket_service.require_staff(interaction)
            ticket = await self.bot.ticket_service.require_ticket(interaction.channel)
        except (RuntimeError, PermissionError) as exc:
            await interaction.response.send_message(embed=EmbedFactory.error(title="Unable to delete", description=str(exc)), ephemeral=True)
            return
        if ticket["status"] != "closed":
            await interaction.response.send_message(embed=EmbedFactory.error(title="Close it first", description="Tickets must be closed before deletion."), ephemeral=True)
            return
        await interaction.response.send_message(
            embed=EmbedFactory.warning(title="Deleting ticket", description="This channel will be deleted in **5 seconds**. The transcript should already be in the ticket log channel."),
            ephemeral=True,
        )
        await asyncio.sleep(5)
        if isinstance(interaction.channel, discord.TextChannel):
            await interaction.channel.delete(reason=f"Closed ticket #{int(ticket['id'])} deleted")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Tickets(bot))
