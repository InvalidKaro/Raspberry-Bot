from __future__ import annotations

import re

import discord
from discord import app_commands
from discord.ext import commands


SUPPORTED_EVENTS = {
    "push",
    "issues",
    "issue_comment",
    "pull_request",
    "pull_request_review",
    "workflow_run",
    "workflow_job",
    "release",
    "create",
    "delete",
}
PRESETS = {
    "all": sorted(SUPPORTED_EVENTS),
    "code": ["push", "pull_request", "pull_request_review"],
    "issues": ["issues", "issue_comment"],
    "ci": ["workflow_run", "workflow_job"],
    "releases": ["release", "create", "delete"],
}
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def _parse_events(raw: str) -> list[str]:
    value = raw.strip().lower()
    if value in PRESETS:
        return PRESETS[value]
    parts = [part.strip().lower() for part in value.split(",") if part.strip()]
    invalid = [item for item in parts if item not in SUPPORTED_EVENTS]
    if invalid:
        raise ValueError("Unbekannte Events: " + ", ".join(invalid))
    return sorted(set(parts))


def _embed(title: str, description: str, color: int = 0x24292F) -> discord.Embed:
    embed = discord.Embed(title=title, description=description, color=color)
    embed.set_footer(text="GitHub Bridge · signed webhook delivery")
    return embed


class GitHubBridge(
    commands.GroupCog,
    group_name="github",
    group_description="GitHub → Discord Webhook Bridge",
):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        await self.bot.database.execute(
            """
            CREATE TABLE IF NOT EXISTS github_subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                repo_full_name TEXT NOT NULL,
                channel_id INTEGER NOT NULL,
                events TEXT NOT NULL DEFAULT 'all',
                enabled INTEGER NOT NULL DEFAULT 1,
                created_by INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(guild_id,repo_full_name,channel_id)
            )
            """
        )
        await self.bot.database.execute(
            """
            CREATE TABLE IF NOT EXISTS github_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                delivery_id TEXT NOT NULL UNIQUE,
                event_type TEXT NOT NULL,
                action TEXT,
                repo_full_name TEXT,
                actor TEXT,
                summary TEXT,
                target_url TEXT,
                dispatched_count INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'received',
                error TEXT,
                received_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

    @app_commands.command(name="subscribe", description="GitHub-Events eines Repositories in einen Channel spiegeln.")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(
        repository="Format owner/repository",
        kanal="Discord-Channel für GitHub-Karten",
        events="Preset: all/code/issues/ci/releases oder CSV einzelner Events",
    )
    async def subscribe(
        self,
        interaction: discord.Interaction,
        repository: str,
        kanal: discord.TextChannel,
        events: str = "all",
    ) -> None:
        if interaction.guild_id is None:
            return
        repo = repository.strip()
        if not REPO_RE.fullmatch(repo):
            await interaction.response.send_message("Repository bitte als `owner/repository` angeben.", ephemeral=True)
            return
        try:
            parsed = _parse_events(events)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        event_text = ",".join(parsed) if parsed != PRESETS["all"] else "all"
        await self.bot.database.execute(
            """
            INSERT INTO github_subscriptions(guild_id,repo_full_name,channel_id,events,enabled,created_by)
            VALUES(?,?,?,?,1,?)
            ON CONFLICT(guild_id,repo_full_name,channel_id) DO UPDATE SET
                events=excluded.events,
                enabled=1,
                created_by=excluded.created_by,
                updated_at=CURRENT_TIMESTAMP
            """,
            (interaction.guild_id, repo, kanal.id, event_text, interaction.user.id),
        )
        event_label = "alle unterstützten Events" if event_text == "all" else ", ".join(parsed)
        await interaction.response.send_message(
            embed=_embed(
                "GitHub Subscription aktiv",
                f"**Repository:** `{repo}`\n"
                f"**Channel:** {kanal.mention}\n"
                f"**Events:** {event_label}\n\n"
                "GitHub muss den Webhook an `https://DEINE-DOMAIN/github/webhook` senden.",
                0x238636,
            ),
            ephemeral=True,
        )

    @app_commands.command(name="unsubscribe", description="GitHub-Subscription für einen Channel entfernen.")
    @app_commands.default_permissions(manage_guild=True)
    async def unsubscribe(
        self,
        interaction: discord.Interaction,
        repository: str,
        kanal: discord.TextChannel,
    ) -> None:
        if interaction.guild_id is None:
            return
        await self.bot.database.execute(
            "DELETE FROM github_subscriptions WHERE guild_id=? AND lower(repo_full_name)=lower(?) AND channel_id=?",
            (interaction.guild_id, repository.strip(), kanal.id),
        )
        await interaction.response.send_message("GitHub-Subscription entfernt.", ephemeral=True)

    @app_commands.command(name="subscriptions", description="Aktive GitHub-Subscriptions anzeigen.")
    async def subscriptions(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            return
        rows = await self.bot.database.fetchall(
            """
            SELECT repo_full_name,channel_id,events,enabled
            FROM github_subscriptions
            WHERE guild_id=?
            ORDER BY repo_full_name COLLATE NOCASE,channel_id
            LIMIT 50
            """,
            (interaction.guild_id,),
        )
        lines = []
        for row in rows:
            state = "🟢" if int(row["enabled"]) else "⚫"
            lines.append(
                f"{state} **{row['repo_full_name']}** → <#{row['channel_id']}>\n"
                f"└ `{row['events']}`"
            )
        await interaction.response.send_message(
            embed=_embed("GitHub Bridge", "\n".join(lines) or "Noch keine Subscriptions."),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name="events", description="Unterstützte GitHub-Webhook-Events anzeigen.")
    async def events(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            embed=_embed(
                "Unterstützte Events",
                "**Code**\n`push` · `pull_request` · `pull_request_review`\n\n"
                "**Issues**\n`issues` · `issue_comment`\n\n"
                "**Actions / CI**\n`workflow_run` · `workflow_job`\n\n"
                "**Release & Refs**\n`release` · `create` · `delete`\n\n"
                "Presets für `/github subscribe`: `all`, `code`, `issues`, `ci`, `releases`.",
            ),
            ephemeral=True,
        )

    @app_commands.command(name="setup_info", description="Zeigt die sichere GitHub-Webhook-Einrichtung.")
    @app_commands.default_permissions(manage_guild=True)
    async def setup_info(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            embed=_embed(
                "GitHub Webhook Setup",
                "1. Im Dashboard-Service `GITHUB_WEBHOOK_SECRET` setzen.\n"
                "2. Den Dashboard-Port über **öffentliches HTTPS** erreichbar machen.\n"
                "3. In GitHub als Payload URL `https://DEINE-DOMAIN/github/webhook` eintragen.\n"
                "4. Content type: `application/json` und denselben Secret-Wert verwenden.\n"
                "5. Danach `/github subscribe` für Repository + Discord-Channel ausführen.\n\n"
                "Der Endpoint akzeptiert nur Requests mit gültiger `X-Hub-Signature-256`.",
                0x1F6FEB,
            ),
            ephemeral=True,
        )

    @app_commands.command(name="recent", description="Zuletzt empfangene GitHub-Webhook-Events anzeigen.")
    @app_commands.default_permissions(manage_guild=True)
    async def recent(self, interaction: discord.Interaction) -> None:
        rows = await self.bot.database.fetchall(
            """
            SELECT event_type,action,repo_full_name,actor,summary,dispatched_count,status,received_at
            FROM github_events
            ORDER BY id DESC
            LIMIT 12
            """
        )
        lines = []
        for row in rows:
            action = f"/{row['action']}" if row["action"] else ""
            lines.append(
                f"`{row['received_at']}` **{row['event_type']}{action}** · "
                f"`{row['repo_full_name'] or 'unknown'}` · {row['status']} · "
                f"{row['dispatched_count']} dispatch"
            )
        await interaction.response.send_message(
            embed=_embed("Recent GitHub Deliveries", "\n".join(lines) or "Noch keine Webhooks empfangen."),
            ephemeral=True,
        )

    @app_commands.command(name="test", description="Testkarte für eine Subscription senden.")
    @app_commands.default_permissions(manage_guild=True)
    async def test(
        self,
        interaction: discord.Interaction,
        repository: str,
        kanal: discord.TextChannel,
    ) -> None:
        if interaction.guild_id is None:
            return
        row = await self.bot.database.fetchone(
            """
            SELECT 1 FROM github_subscriptions
            WHERE guild_id=? AND lower(repo_full_name)=lower(?) AND channel_id=? AND enabled=1
            """,
            (interaction.guild_id, repository.strip(), kanal.id),
        )
        if not row:
            await interaction.response.send_message("Für diesen Channel existiert keine aktive Subscription.", ephemeral=True)
            return
        embed = _embed(
            "✅ GitHub Bridge Test",
            f"**{repository.strip()}** ist mit {kanal.mention} verbunden.\n\n"
            "Pushes, Issues, Pull Requests und Actions können hier als Live-Karten erscheinen.",
            0x238636,
        )
        await kanal.send(embed=embed)
        await interaction.response.send_message("Testkarte gesendet.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(GitHubBridge(bot))
