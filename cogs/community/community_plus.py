from __future__ import annotations

import asyncio
import logging
import math
import random
import time
from datetime import UTC, datetime, timedelta

import discord
from discord import app_commands
from discord.ext import commands

from helpers.embeds import EmbedFactory

logger = logging.getLogger(__name__)

XP_COOLDOWN_SECONDS = 45
XP_MIN = 5
XP_MAX = 10


class GiveawayJoinButton(discord.ui.Button):
    def __init__(self, giveaway_id: int) -> None:
        self.giveaway_id = int(giveaway_id)
        super().__init__(
            label="Teilnehmen",
            emoji="🎉",
            style=discord.ButtonStyle.success,
            custom_id=f"suite:giveaway:{self.giveaway_id}",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        bot = interaction.client
        row = await bot.database.fetchone(
            "SELECT status,ends_at FROM giveaways WHERE id=?",
            (self.giveaway_id,),
        )
        if not row or str(row["status"]) != "open":
            await interaction.response.send_message("Dieses Giveaway ist beendet.", ephemeral=True)
            return
        await bot.database.execute(
            "INSERT OR IGNORE INTO giveaway_entries(giveaway_id,user_id) VALUES(?,?)",
            (self.giveaway_id, interaction.user.id),
        )
        await interaction.response.send_message("Du nimmst am Giveaway teil.", ephemeral=True)


class GiveawayView(discord.ui.View):
    def __init__(self, giveaway_id: int) -> None:
        super().__init__(timeout=None)
        self.add_item(GiveawayJoinButton(giveaway_id))


class CommunityPlus(
    commands.GroupCog,
    group_name="community",
    group_description="XP, Achievements, Quotes und Giveaways",
):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._xp_seen: dict[tuple[int, int], float] = {}
        self._giveaway_task: asyncio.Task | None = None
        self._xp_events = 0
        self._xp_errors = 0

    async def cog_load(self) -> None:
        active = await self.bot.database.fetchall(
            "SELECT id,message_id FROM giveaways WHERE status='open' AND message_id IS NOT NULL LIMIT 100"
        )
        for row in active:
            self.bot.add_view(
                GiveawayView(int(row["id"])),
                message_id=int(row["message_id"]),
            )
        self._giveaway_task = asyncio.create_task(
            self._giveaway_loop(),
            name="giveaway-suite-loop",
        )
        logger.info("CommunityPlus loaded; XP message tracking is active")

    async def cog_unload(self) -> None:
        if self._giveaway_task:
            self._giveaway_task.cancel()

    async def _ensure_achievements(self, guild_id: int) -> None:
        defaults = [
            ("starter", "Starter", "100 XP erreicht", 100),
            ("active", "Aktiv", "500 XP erreicht", 500),
            ("veteran", "Veteran", "1.500 XP erreicht", 1500),
            ("legend", "Legende", "5.000 XP erreicht", 5000),
        ]
        for key, title, desc, threshold in defaults:
            await self.bot.database.execute(
                "INSERT OR IGNORE INTO achievements(guild_id,achievement_key,title,description,threshold_xp,created_by) VALUES(?,?,?,?,?,0)",
                (guild_id, key, title, desc, threshold),
            )

    async def _unlock_achievements(self, guild_id: int, user_id: int, xp: int) -> None:
        await self._ensure_achievements(guild_id)
        rows = await self.bot.database.fetchall(
            "SELECT id FROM achievements WHERE guild_id=? AND threshold_xp IS NOT NULL AND threshold_xp<=?",
            (guild_id, xp),
        )
        for row in rows:
            await self.bot.database.execute(
                "INSERT OR IGNORE INTO user_achievements(guild_id,user_id,achievement_id) VALUES(?,?,?)",
                (guild_id, user_id, int(row["id"])),
            )

    async def _award_xp(self, guild_id: int, user_id: int, gain: int) -> tuple[int, int, int]:
        gain = max(0, int(gain))
        await self.bot.database.execute(
            """
            INSERT INTO xp_profiles(guild_id,user_id,xp,message_count,level,updated_at)
            VALUES(?,?,?,1,0,CURRENT_TIMESTAMP)
            ON CONFLICT(guild_id,user_id) DO UPDATE SET
                xp=xp+excluded.xp,
                message_count=message_count+1,
                updated_at=CURRENT_TIMESTAMP
            """,
            (guild_id, user_id, gain),
        )
        row = await self.bot.database.fetchone(
            "SELECT xp,level,message_count FROM xp_profiles WHERE guild_id=? AND user_id=?",
            (guild_id, user_id),
        )
        if not row:
            raise RuntimeError("XP profile could not be read after write")
        xp = int(row["xp"])
        old_level = int(row["level"])
        messages = int(row["message_count"])
        new_level = int(math.sqrt(xp / 100))
        if new_level != old_level:
            await self.bot.database.execute(
                "UPDATE xp_profiles SET level=?,updated_at=CURRENT_TIMESTAMP WHERE guild_id=? AND user_id=?",
                (new_level, guild_id, user_id),
            )
        await self._unlock_achievements(guild_id, user_id, xp)
        return xp, new_level, messages

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot or message.webhook_id is not None:
            return

        key = (message.guild.id, message.author.id)
        now = time.monotonic()
        if now - self._xp_seen.get(key, 0.0) < XP_COOLDOWN_SECONDS:
            return

        # Set the cooldown before the DB write so duplicate MESSAGE_CREATE events
        # cannot award twice. On failure it is removed so the next message retries.
        self._xp_seen[key] = now
        gain = random.randint(XP_MIN, XP_MAX)
        try:
            await self._award_xp(message.guild.id, message.author.id, gain)
            self._xp_events += 1
        except Exception:
            self._xp_errors += 1
            self._xp_seen.pop(key, None)
            logger.exception(
                "XP award failed for guild=%s user=%s",
                message.guild.id,
                message.author.id,
            )
            return

        if len(self._xp_seen) > 3000:
            cutoff = now - 3600
            self._xp_seen = {k: v for k, v in self._xp_seen.items() if v >= cutoff}

    async def _profile_embed(self, guild_id: int, member: discord.abc.User) -> discord.Embed:
        row = await self.bot.database.fetchone(
            "SELECT * FROM xp_profiles WHERE guild_id=? AND user_id=?",
            (guild_id, member.id),
        )
        xp = int(row["xp"]) if row else 0
        level = int(row["level"]) if row else 0
        messages = int(row["message_count"]) if row else 0
        achievements = await self.bot.database.fetchall(
            """
            SELECT a.title
            FROM user_achievements ua
            JOIN achievements a ON a.id=ua.achievement_id
            WHERE ua.guild_id=? AND ua.user_id=?
            ORDER BY ua.unlocked_at DESC
            LIMIT 8
            """,
            (guild_id, member.id),
        )
        badge_text = ", ".join(str(r["title"]) for r in achievements) or "—"
        embed = EmbedFactory.info(
            title=f"Community Profil · {getattr(member, 'display_name', member.name)}",
            description=f"**Level {level}** · **{xp} XP** · {messages} gewertete Nachrichten",
        )
        embed.add_field(name="Achievements", value=badge_text, inline=False)
        embed.set_footer(
            text=f"XP: {XP_MIN}–{XP_MAX} pro gewerteter Nachricht · Cooldown {XP_COOLDOWN_SECONDS}s"
        )
        return embed

    async def _finish_giveaway(self, row) -> list[int]:
        entries = await self.bot.database.fetchall(
            "SELECT user_id FROM giveaway_entries WHERE giveaway_id=?",
            (row["id"],),
        )
        users = [int(r["user_id"]) for r in entries]
        count = min(int(row["winner_count"]), len(users))
        winners = random.sample(users, count) if count else []
        await self.bot.database.execute(
            "UPDATE giveaways SET status='ended',ended_at=CURRENT_TIMESTAMP WHERE id=?",
            (row["id"],),
        )
        channel = self.bot.get_channel(int(row["channel_id"])) if row["channel_id"] else None
        if isinstance(channel, discord.abc.Messageable):
            if winners:
                await channel.send(
                    f"🎉 Giveaway **{row['prize']}** beendet. Gewinner: "
                    + ", ".join(f"<@{uid}>" for uid in winners)
                )
            else:
                await channel.send(f"Giveaway **{row['prize']}** beendet – keine Teilnehmer.")
        return winners

    async def _giveaway_loop(self) -> None:
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            rows = await self.bot.database.fetchall(
                "SELECT * FROM giveaways WHERE status='open' AND ends_at<=? ORDER BY ends_at LIMIT 10",
                (datetime.now(UTC).isoformat(),),
            )
            for row in rows:
                try:
                    await self._finish_giveaway(row)
                except Exception:
                    logger.exception("Failed to finish giveaway %s", row["id"])
            await asyncio.sleep(30)

    @app_commands.command(name="profile", description="XP-/Community-Profil anzeigen.")
    async def profile(
        self,
        interaction: discord.Interaction,
        mitglied: discord.Member | None = None,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message("XP funktioniert nur auf einem Server.", ephemeral=True)
            return
        member = mitglied or interaction.user
        await interaction.response.send_message(
            embed=await self._profile_embed(interaction.guild_id, member)
        )

    @app_commands.command(name="xp", description="XP eines Mitglieds direkt abfragen.")
    async def xp(
        self,
        interaction: discord.Interaction,
        mitglied: discord.Member | None = None,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message("XP funktioniert nur auf einem Server.", ephemeral=True)
            return
        member = mitglied or interaction.user
        await interaction.response.send_message(
            embed=await self._profile_embed(interaction.guild_id, member),
            ephemeral=True,
        )

    @app_commands.command(name="xp_status", description="XP-System und Datensammlung prüfen.")
    @app_commands.default_permissions(manage_guild=True)
    async def xp_status(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message("Nur auf einem Server nutzbar.", ephemeral=True)
            return
        row = await self.bot.database.fetchone(
            "SELECT COUNT(*) profiles,COALESCE(SUM(xp),0) total_xp,COALESCE(SUM(message_count),0) messages,MAX(updated_at) last_update FROM xp_profiles WHERE guild_id=?",
            (interaction.guild_id,),
        )
        embed = EmbedFactory.info(
            title="XP Systemstatus",
            description=(
                f"**Tracker:** geladen\n"
                f"**MESSAGE intent:** {'aktiv' if self.bot.intents.messages else 'aus'}\n"
                f"**Message Content intent:** {'aktiv' if self.bot.intents.message_content else 'aus'}\n"
                f"**Profile:** {int(row['profiles'] or 0)}\n"
                f"**Gesamt-XP:** {int(row['total_xp'] or 0)}\n"
                f"**Gewertete Nachrichten:** {int(row['messages'] or 0)}\n"
                f"**Letztes DB-Update:** {row['last_update'] or '—'}\n"
                f"**XP-Events seit Botstart:** {self._xp_events}\n"
                f"**XP-Fehler seit Botstart:** {self._xp_errors}\n"
                f"**Cooldown:** {XP_COOLDOWN_SECONDS}s"
            ),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="xp_add", description="XP manuell hinzufügen, z. B. zum Testen.")
    @app_commands.default_permissions(administrator=True)
    async def xp_add(
        self,
        interaction: discord.Interaction,
        mitglied: discord.Member,
        xp: app_commands.Range[int, 1, 100000],
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message("Nur auf einem Server nutzbar.", ephemeral=True)
            return
        total, level, messages = await self._award_xp(
            interaction.guild_id,
            mitglied.id,
            int(xp),
        )
        await interaction.response.send_message(
            f"**{mitglied.display_name}** +{int(xp)} XP → **{total} XP**, Level **{level}**. "
            f"Gewertete Nachrichten: {messages}.",
            ephemeral=True,
        )

    @app_commands.command(name="leaderboard", description="XP-Leaderboard anzeigen.")
    async def leaderboard(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message("Nur auf einem Server nutzbar.", ephemeral=True)
            return
        rows = await self.bot.database.fetchall(
            "SELECT user_id,xp,level FROM xp_profiles WHERE guild_id=? ORDER BY xp DESC LIMIT 15",
            (interaction.guild_id,),
        )
        lines = [
            f"**{i}.** <@{r['user_id']}> · Level **{r['level']}** · **{r['xp']} XP**"
            for i, r in enumerate(rows, 1)
        ]
        await interaction.response.send_message(
            embed=EmbedFactory.info(
                title="XP Leaderboard",
                description="\n".join(lines) or "Noch keine XP-Daten.",
            )
        )

    @app_commands.command(name="achievement_add", description="Eigenes XP-Achievement anlegen.")
    @app_commands.default_permissions(manage_messages=True)
    async def achievement_add(
        self,
        interaction: discord.Interaction,
        key: str,
        titel: str,
        beschreibung: str,
        xp: app_commands.Range[int, 1, 100000],
    ) -> None:
        await self.bot.database.execute(
            """
            INSERT INTO achievements(guild_id,achievement_key,title,description,threshold_xp,created_by)
            VALUES(?,?,?,?,?,?)
            ON CONFLICT(guild_id,achievement_key) DO UPDATE SET
                title=excluded.title,
                description=excluded.description,
                threshold_xp=excluded.threshold_xp,
                created_by=excluded.created_by
            """,
            (
                interaction.guild_id,
                key.strip().lower(),
                titel.strip(),
                beschreibung.strip(),
                int(xp),
                interaction.user.id,
            ),
        )
        await interaction.response.send_message("Achievement gespeichert.", ephemeral=True)

    @app_commands.command(name="achievements", description="Verfügbare Achievements anzeigen.")
    async def achievements(self, interaction: discord.Interaction) -> None:
        await self._ensure_achievements(interaction.guild_id)
        rows = await self.bot.database.fetchall(
            "SELECT title,description,threshold_xp FROM achievements WHERE guild_id=? ORDER BY threshold_xp,id",
            (interaction.guild_id,),
        )
        lines = [
            f"🏅 **{r['title']}** · {r['threshold_xp']} XP\n{r['description']}"
            for r in rows[:20]
        ]
        await interaction.response.send_message(
            embed=EmbedFactory.info(title="Achievements", description="\n".join(lines))
        )

    @app_commands.command(name="quote_add", description="Zitat speichern.")
    async def quote_add(
        self,
        interaction: discord.Interaction,
        text: str,
        autor: discord.Member | None = None,
    ) -> None:
        quote_id = await self.bot.database.execute(
            "INSERT INTO quotes(guild_id,author_text,content,source_user_id,created_by) VALUES(?,?,?,?,?)",
            (
                interaction.guild_id,
                autor.display_name if autor else interaction.user.display_name,
                text.strip(),
                autor.id if autor else interaction.user.id,
                interaction.user.id,
            ),
        )
        await interaction.response.send_message(f"Zitat `#{quote_id}` gespeichert.", ephemeral=True)

    @app_commands.command(name="quote", description="Zufälliges oder bestimmtes Zitat anzeigen.")
    async def quote(self, interaction: discord.Interaction, quote_id: int | None = None) -> None:
        if quote_id is not None:
            row = await self.bot.database.fetchone(
                "SELECT * FROM quotes WHERE guild_id=? AND id=?",
                (interaction.guild_id, quote_id),
            )
        else:
            row = await self.bot.database.fetchone(
                "SELECT * FROM quotes WHERE guild_id=? ORDER BY RANDOM() LIMIT 1",
                (interaction.guild_id,),
            )
        if not row:
            await interaction.response.send_message("Kein Zitat gefunden.", ephemeral=True)
            return
        await interaction.response.send_message(
            embed=EmbedFactory.info(
                title=f"Zitat #{row['id']}",
                description=f"“{str(row['content'])[:3500]}”\n\n— **{row['author_text']}**",
            )
        )

    @app_commands.command(name="giveaway_start", description="Giveaway mit Teilnahme-Button starten.")
    @app_commands.default_permissions(manage_messages=True)
    async def giveaway_start(
        self,
        interaction: discord.Interaction,
        preis: str,
        minuten: app_commands.Range[int, 1, 10080],
        gewinner: app_commands.Range[int, 1, 20] = 1,
        kanal: discord.TextChannel | None = None,
    ) -> None:
        target = kanal or interaction.channel
        if not isinstance(target, discord.abc.Messageable):
            await interaction.response.send_message("Kein gültiger Zielkanal.", ephemeral=True)
            return
        ends = datetime.now(UTC) + timedelta(minutes=int(minuten))
        giveaway_id = await self.bot.database.execute(
            "INSERT INTO giveaways(guild_id,channel_id,prize,ends_at,winner_count,status,created_by) VALUES(?,?,?,?,?,'open',?)",
            (
                interaction.guild_id,
                target.id,
                preis.strip(),
                ends.isoformat(),
                int(gewinner),
                interaction.user.id,
            ),
        )
        view = GiveawayView(giveaway_id)
        embed = EmbedFactory.info(
            title="🎉 Giveaway",
            description=(
                f"**{preis}**\n\nEnde: <t:{int(ends.timestamp())}:R>\n"
                f"Gewinner: **{gewinner}**\n\nMit dem Button teilnehmen."
            ),
        )
        msg = await target.send(embed=embed, view=view)
        await self.bot.database.execute(
            "UPDATE giveaways SET message_id=? WHERE id=?",
            (msg.id, giveaway_id),
        )
        self.bot.add_view(view, message_id=msg.id)
        await interaction.response.send_message(
            f"Giveaway `#{giveaway_id}` gestartet.",
            ephemeral=True,
        )

    @app_commands.command(name="giveaway_end", description="Giveaway sofort beenden.")
    @app_commands.default_permissions(manage_messages=True)
    async def giveaway_end(
        self,
        interaction: discord.Interaction,
        giveaway_id: int,
    ) -> None:
        row = await self.bot.database.fetchone(
            "SELECT * FROM giveaways WHERE id=? AND guild_id=? AND status='open'",
            (giveaway_id, interaction.guild_id),
        )
        if not row:
            await interaction.response.send_message("Offenes Giveaway nicht gefunden.", ephemeral=True)
            return
        winners = await self._finish_giveaway(row)
        await interaction.response.send_message(
            "Beendet. Gewinner: "
            + (", ".join(f"<@{x}>" for x in winners) if winners else "keine"),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CommunityPlus(bot))
