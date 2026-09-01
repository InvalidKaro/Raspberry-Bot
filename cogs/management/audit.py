from __future__ import annotations

import asyncio
import os
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from helpers.embeds import EmbedFactory
from services.backups import BackupService


class Audit(commands.GroupCog, group_name="audit", group_description="Raspberry-Bot audit trail"):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="recent", description="Recent bot configuration/personnel/admin changes.")
    @app_commands.default_permissions(manage_guild=True)
    async def recent(self, i: discord.Interaction, limit: app_commands.Range[int, 1, 25] = 15):
        rows = await self.bot.database.fetchall(
            "SELECT * FROM bot_audit_log WHERE guild_id=? ORDER BY id DESC LIMIT ?",
            (i.guild_id, limit),
        )
        lines = [
            f"**#{r['id']} {r['action']}** · <@{r['actor_id']}>\n└ `{r['created_at']}` · {r['target_type'] or '—'} `{r['target_id'] or '—'}`"
            for r in rows
        ]
        await i.response.send_message(
            embed=EmbedFactory.info(title="Audit Trail", description="\n\n".join(lines) or "No entries."),
            ephemeral=True,
        )


class Phase4Extensions(commands.Cog):
    """Adds Phase 4 subcommands to existing /ticket, /mod and /dev groups."""

    def __init__(self, bot):
        self.bot = bot
        self.backups = BackupService(bot.database)
        self.added: list[tuple[app_commands.Group, str]] = []

    async def cog_load(self) -> None:
        ticket = self.bot.tree.get_command("ticket")
        mod = self.bot.tree.get_command("mod")
        dev = self.bot.tree.get_command("dev")
        if isinstance(ticket, app_commands.Group):
            self._add(ticket, app_commands.Command(name="stats", description="Show ticket workload and feedback statistics.", callback=self.ticket_stats))
            self._add(ticket, app_commands.Command(name="feedback", description="Rate this ticket from 1 to 5 stars.", callback=self.ticket_feedback))
        if isinstance(mod, app_commands.Group):
            self._add(mod, app_commands.Command(name="history", description="Show moderation history for a member.", callback=self.mod_history))
            self._add(mod, app_commands.Command(name="escalation", description="Show warning escalation recommendation.", callback=self.mod_escalation))
        if isinstance(dev, app_commands.Group):
            self._add(dev, app_commands.Command(name="backup", description="Create an immediate SQLite backup.", callback=self.dev_backup))
            self._add(dev, app_commands.Command(name="backups", description="List available SQLite backups.", callback=self.dev_backups))
            self._add(dev, app_commands.Command(name="maintenance", description="Enable or disable maintenance mode.", callback=self.dev_maintenance))
            self._add(dev, app_commands.Command(name="doctor", description="Run full Raspberry-Bot self diagnostics.", callback=self.dev_doctor))
            self._add(dev, app_commands.Command(name="healthcheck", description="Owner healthcheck for bot/dashboard/database.", callback=self.dev_healthcheck))

    async def cog_unload(self) -> None:
        for group, name in self.added:
            group.remove_command(name)
        self.added.clear()

    def _add(self, group: app_commands.Group, command: app_commands.Command) -> None:
        existing = group.get_command(command.name)
        if existing is not None:
            return
        group.add_command(command)
        self.added.append((group, command.name))

    async def _owner(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id in self.bot.settings.owner_ids:
            return True
        await interaction.response.send_message("Owner only.", ephemeral=True)
        return False

    async def ticket_stats(self, interaction: discord.Interaction) -> None:
        if not interaction.guild_id:
            return
        guild_id = interaction.guild_id
        total = await self.bot.database.fetchone("SELECT COUNT(*) c FROM tickets WHERE guild_id=?", (guild_id,))
        opened = await self.bot.database.fetchone("SELECT COUNT(*) c FROM tickets WHERE guild_id=? AND status='open'", (guild_id,))
        claimed = await self.bot.database.fetchone("SELECT COUNT(*) c FROM tickets WHERE guild_id=? AND status='open' AND claimed_by IS NOT NULL", (guild_id,))
        fb = await self.bot.database.fetchone("SELECT COUNT(*) c, AVG(rating) avg FROM ticket_feedback WHERE guild_id=?", (guild_id,))
        bystaff = await self.bot.database.fetchall(
            "SELECT claimed_by,COUNT(*) c FROM tickets WHERE guild_id=? AND claimed_by IS NOT NULL GROUP BY claimed_by ORDER BY c DESC LIMIT 8",
            (guild_id,),
        )
        e = EmbedFactory.info(
            title="Ticket Statistics",
            description=f"Total: **{total['c']}**\nOpen: **{opened['c']}**\nClaimed open: **{claimed['c']}**\nFeedback: **{fb['c']}** · Ø **{float(fb['avg'] or 0):.2f}/5**",
        )
        if bystaff:
            e.add_field(name="Top claims", value="\n".join(f"<@{r['claimed_by']}> · **{r['c']}**" for r in bystaff), inline=False)
        await interaction.response.send_message(embed=e, ephemeral=True)

    async def ticket_feedback(self, interaction: discord.Interaction, rating: app_commands.Range[int, 1, 5], comment: str | None = None) -> None:
        ticket = await self.bot.database.fetchone("SELECT * FROM tickets WHERE channel_id=?", (interaction.channel_id,))
        if not ticket:
            await interaction.response.send_message("This channel is not linked to a ticket.", ephemeral=True)
            return
        if int(ticket["opener_id"]) != interaction.user.id:
            await interaction.response.send_message("Only the ticket opener can submit feedback.", ephemeral=True)
            return
        await self.bot.database.execute(
            """INSERT INTO ticket_feedback(ticket_id,guild_id,user_id,rating,comment) VALUES(?,?,?,?,?)
            ON CONFLICT(ticket_id) DO UPDATE SET rating=excluded.rating,comment=excluded.comment,created_at=CURRENT_TIMESTAMP""",
            (int(ticket["id"]), interaction.guild_id, interaction.user.id, rating, comment),
        )
        await interaction.response.send_message(f"Feedback saved: {'⭐' * rating}", ephemeral=True)

    async def mod_history(self, interaction: discord.Interaction, member: discord.Member) -> None:
        rows = await self.bot.database.fetchall(
            "SELECT * FROM moderation_cases WHERE guild_id=? AND user_id=? ORDER BY id DESC LIMIT 25",
            (interaction.guild_id, member.id),
        )
        lines = [
            f"**#{r['id']}** · `{r['action']}` · {'active' if int(r['active']) else 'inactive'}\n└ {r['reason'] or 'No reason'} · <@{r['moderator_id']}> · `{r['created_at']}`"
            for r in rows
        ]
        await interaction.response.send_message(
            embed=EmbedFactory.info(title=f"Moderation History • {member}", description="\n\n".join(lines) or "No cases stored."),
            ephemeral=True,
        )

    async def mod_escalation(self, interaction: discord.Interaction, member: discord.Member) -> None:
        row = await self.bot.database.fetchone(
            "SELECT COUNT(*) c FROM moderation_cases WHERE guild_id=? AND user_id=? AND action='warn' AND active=1",
            (interaction.guild_id, member.id),
        )
        count = int(row["c"] if row else 0)
        if count <= 1:
            rec = "No escalation"
        elif count == 2:
            rec = "Consider a short timeout"
        elif count == 3:
            rec = "Recommended timeout: 30–60 minutes"
        elif count == 4:
            rec = "Recommended timeout: 12–24 hours"
        else:
            rec = "Senior moderator review / possible ban"
        await interaction.response.send_message(
            embed=EmbedFactory.info(title="Escalation Check", description=f"{member.mention} has **{count} active warning(s)**.\n\n**Recommendation:** {rec}"),
            ephemeral=True,
        )

    async def dev_backup(self, interaction: discord.Interaction) -> None:
        if not await self._owner(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        path = await self.backups.create(kind="manual", created_by=interaction.user.id)
        await interaction.followup.send(f"Backup created: `{path.name}` · {path.stat().st_size / 1024:.1f} KiB", ephemeral=True)

    async def dev_backups(self, interaction: discord.Interaction) -> None:
        if not await self._owner(interaction):
            return
        rows = await self.backups.list()
        text = "\n".join(f"`{p.name}` · {p.stat().st_size / 1024:.1f} KiB" for p in rows[:20]) or "No backups."
        await interaction.response.send_message(embed=EmbedFactory.info(title="Backups", description=text), ephemeral=True)

    async def dev_maintenance(self, interaction: discord.Interaction, state: str, reason: str | None = None) -> None:
        if not await self._owner(interaction):
            return
        enabled = 1 if state.lower() in {"on", "true", "1", "enable", "enabled"} else 0
        await self.bot.database.execute(
            "UPDATE maintenance_state SET enabled=?,enabled_by=?,reason=?,updated_at=CURRENT_TIMESTAMP WHERE id=1",
            (enabled, interaction.user.id, reason),
        )
        await interaction.response.send_message(f"Maintenance mode: **{'ON' if enabled else 'OFF'}**", ephemeral=True)

    async def dev_doctor(self, interaction: discord.Interaction) -> None:
        if not await self._owner(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        checks: list[tuple[str, bool, str]] = []
        try:
            row = await self.bot.database.fetchone("PRAGMA integrity_check")
            detail = str(row[0] if row else "unknown")
            checks.append(("SQLite", detail == "ok", detail))
        except Exception as exc:
            checks.append(("SQLite", False, str(exc)))
        dbpath = Path(self.bot.database.path)
        checks.append(("DB writable", dbpath.parent.exists() and os.access(dbpath.parent, os.W_OK), str(dbpath)))
        for name, cmd in (
            ("Pi-hole", ["systemctl", "is-active", "pihole-FTL"]),
            ("Tailscale", ["systemctl", "is-active", "tailscaled"]),
            ("Dashboard", ["systemctl", "is-active", "raspberry-dashboard"]),
        ):
            try:
                proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
                out, _ = await asyncio.wait_for(proc.communicate(), timeout=4)
                detail = out.decode().strip()
                checks.append((name, proc.returncode == 0, detail))
            except Exception as exc:
                checks.append((name, False, str(exc)))
        e = EmbedFactory.info(title="Self Diagnostics", description="\n".join(("✅" if ok else "❌") + f" **{name}** — `{detail[:100]}`" for name, ok, detail in checks))
        await interaction.followup.send(embed=e, ephemeral=True)

    async def dev_healthcheck(self, interaction: discord.Interaction) -> None:
        if not await self._owner(interaction):
            return
        row = await self.bot.database.fetchone("SELECT COUNT(*) c FROM sqlite_master WHERE type='table'")
        await interaction.response.send_message(
            embed=EmbedFactory.info(
                title="Owner Healthcheck",
                description=f"Bot: **online**\nGuilds: **{len(self.bot.guilds)}**\nLatency: **{self.bot.latency * 1000:.0f} ms**\nDB tables: **{row['c']}**",
            ),
            ephemeral=True,
        )


async def setup(bot):
    await bot.add_cog(Audit(bot))
    await bot.add_cog(Phase4Extensions(bot))
