from __future__ import annotations

import asyncio
import math
import random
import time
from datetime import UTC, datetime, timedelta

import discord
from discord import app_commands
from discord.ext import commands

from helpers.embeds import EmbedFactory


class GiveawayJoinButton(discord.ui.Button):
    def __init__(self, giveaway_id: int) -> None:
        self.giveaway_id = int(giveaway_id)
        super().__init__(label="Teilnehmen", emoji="🎉", style=discord.ButtonStyle.success, custom_id=f"suite:giveaway:{self.giveaway_id}")

    async def callback(self, interaction: discord.Interaction) -> None:
        bot = interaction.client
        row = await bot.database.fetchone("SELECT status,ends_at FROM giveaways WHERE id=?", (self.giveaway_id,))
        if not row or str(row["status"]) != "open":
            await interaction.response.send_message("Dieses Giveaway ist beendet.", ephemeral=True); return
        await bot.database.execute("INSERT OR IGNORE INTO giveaway_entries(giveaway_id,user_id) VALUES(?,?)", (self.giveaway_id, interaction.user.id))
        await interaction.response.send_message("Du nimmst am Giveaway teil.", ephemeral=True)


class GiveawayView(discord.ui.View):
    def __init__(self, giveaway_id: int) -> None:
        super().__init__(timeout=None); self.add_item(GiveawayJoinButton(giveaway_id))


class CommunityPlus(commands.GroupCog, group_name="community", group_description="XP, Achievements, Quotes und Giveaways"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot; self._xp_seen: dict[tuple[int, int], float] = {}; self._giveaway_task: asyncio.Task | None = None

    async def cog_load(self) -> None:
        active = await self.bot.database.fetchall("SELECT id,message_id FROM giveaways WHERE status='open' AND message_id IS NOT NULL LIMIT 100")
        for row in active: self.bot.add_view(GiveawayView(int(row["id"])), message_id=int(row["message_id"]))
        self._giveaway_task = asyncio.create_task(self._giveaway_loop(), name="giveaway-suite-loop")

    async def cog_unload(self) -> None:
        if self._giveaway_task: self._giveaway_task.cancel()

    async def _ensure_achievements(self, guild_id: int) -> None:
        defaults=[("starter","Starter","100 XP erreicht",100),("active","Aktiv","500 XP erreicht",500),("veteran","Veteran","1.500 XP erreicht",1500),("legend","Legende","5.000 XP erreicht",5000)]
        for key,title,desc,threshold in defaults:
            await self.bot.database.execute("INSERT OR IGNORE INTO achievements(guild_id,achievement_key,title,description,threshold_xp,created_by) VALUES(?,?,?,?,?,0)",(guild_id,key,title,desc,threshold))

    async def _unlock_achievements(self, guild_id: int, user_id: int, xp: int) -> None:
        await self._ensure_achievements(guild_id)
        rows=await self.bot.database.fetchall("SELECT id FROM achievements WHERE guild_id=? AND threshold_xp IS NOT NULL AND threshold_xp<=?",(guild_id,xp))
        for row in rows: await self.bot.database.execute("INSERT OR IGNORE INTO user_achievements(guild_id,user_id,achievement_id) VALUES(?,?,?)",(guild_id,user_id,int(row["id"])))

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if not message.guild or message.author.bot: return
        key=(message.guild.id,message.author.id); now=time.monotonic()
        if now-self._xp_seen.get(key,0.0)<45: return
        self._xp_seen[key]=now; gain=random.randint(5,10)
        await self.bot.database.execute("INSERT INTO xp_profiles(guild_id,user_id,xp,message_count,level,updated_at) VALUES(?,?,?,1,0,CURRENT_TIMESTAMP) ON CONFLICT(guild_id,user_id) DO UPDATE SET xp=xp+excluded.xp,message_count=message_count+1,updated_at=CURRENT_TIMESTAMP",(message.guild.id,message.author.id,gain))
        row=await self.bot.database.fetchone("SELECT xp,level FROM xp_profiles WHERE guild_id=? AND user_id=?",(message.guild.id,message.author.id))
        if not row: return
        xp=int(row["xp"]); level=int(math.sqrt(xp/100))
        if level!=int(row["level"]): await self.bot.database.execute("UPDATE xp_profiles SET level=? WHERE guild_id=? AND user_id=?",(level,message.guild.id,message.author.id))
        await self._unlock_achievements(message.guild.id,message.author.id,xp)
        if len(self._xp_seen)>3000:
            cutoff=now-3600; self._xp_seen={k:v for k,v in self._xp_seen.items() if v>=cutoff}

    async def _finish_giveaway(self,row) -> list[int]:
        entries=await self.bot.database.fetchall("SELECT user_id FROM giveaway_entries WHERE giveaway_id=?",(row["id"],)); users=[int(r["user_id"]) for r in entries]; count=min(int(row["winner_count"]),len(users)); winners=random.sample(users,count) if count else []
        await self.bot.database.execute("UPDATE giveaways SET status='ended',ended_at=CURRENT_TIMESTAMP WHERE id=?",(row["id"],)); channel=self.bot.get_channel(int(row["channel_id"])) if row["channel_id"] else None
        if isinstance(channel,discord.abc.Messageable):
            if winners: await channel.send(f"🎉 Giveaway **{row['prize']}** beendet. Gewinner: {', '.join(f'<@{uid}>' for uid in winners)}")
            else: await channel.send(f"Giveaway **{row['prize']}** beendet – keine Teilnehmer.")
        return winners

    async def _giveaway_loop(self) -> None:
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            rows=await self.bot.database.fetchall("SELECT * FROM giveaways WHERE status='open' AND ends_at<=? ORDER BY ends_at LIMIT 10",(datetime.now(UTC).isoformat(),))
            for row in rows:
                try: await self._finish_giveaway(row)
                except Exception: pass
            await asyncio.sleep(30)

    @app_commands.command(name="profile", description="XP-/Community-Profil anzeigen.")
    async def profile(self, interaction: discord.Interaction, mitglied: discord.Member | None = None) -> None:
        member=mitglied or interaction.user; row=await self.bot.database.fetchone("SELECT * FROM xp_profiles WHERE guild_id=? AND user_id=?",(interaction.guild_id,member.id)); xp=int(row["xp"]) if row else 0; level=int(row["level"]) if row else 0; messages=int(row["message_count"]) if row else 0
        achievements=await self.bot.database.fetchall("SELECT a.title FROM user_achievements ua JOIN achievements a ON a.id=ua.achievement_id WHERE ua.guild_id=? AND ua.user_id=? ORDER BY ua.unlocked_at DESC LIMIT 8",(interaction.guild_id,member.id)); badge_text=", ".join(str(r["title"]) for r in achievements) or "—"
        embed=EmbedFactory.info(title=f"Community Profil · {member.display_name}",description=f"**Level {level}** · **{xp} XP** · {messages} gewertete Nachrichten"); embed.add_field(name="Achievements",value=badge_text,inline=False); await interaction.response.send_message(embed=embed)

    @app_commands.command(name="leaderboard", description="XP-Leaderboard anzeigen.")
    async def leaderboard(self, interaction: discord.Interaction) -> None:
        rows=await self.bot.database.fetchall("SELECT user_id,xp,level FROM xp_profiles WHERE guild_id=? ORDER BY xp DESC LIMIT 15",(interaction.guild_id,)); lines=[f"**{i}.** <@{r['user_id']}> · Level **{r['level']}** · **{r['xp']} XP**" for i,r in enumerate(rows,1)]; await interaction.response.send_message(embed=EmbedFactory.info(title="XP Leaderboard",description="\n".join(lines) or "Noch keine XP-Daten."))

    @app_commands.command(name="achievement_add", description="Eigenes XP-Achievement anlegen.")
    @app_commands.default_permissions(manage_messages=True)
    async def achievement_add(self, interaction: discord.Interaction, key: str, titel: str, beschreibung: str, xp: app_commands.Range[int,1,100000]) -> None:
        await self.bot.database.execute("INSERT INTO achievements(guild_id,achievement_key,title,description,threshold_xp,created_by) VALUES(?,?,?,?,?,?) ON CONFLICT(guild_id,achievement_key) DO UPDATE SET title=excluded.title,description=excluded.description,threshold_xp=excluded.threshold_xp,created_by=excluded.created_by",(interaction.guild_id,key.strip().lower(),titel.strip(),beschreibung.strip(),int(xp),interaction.user.id)); await interaction.response.send_message("Achievement gespeichert.",ephemeral=True)

    @app_commands.command(name="achievements", description="Verfügbare Achievements anzeigen.")
    async def achievements(self, interaction: discord.Interaction) -> None:
        await self._ensure_achievements(interaction.guild_id); rows=await self.bot.database.fetchall("SELECT title,description,threshold_xp FROM achievements WHERE guild_id=? ORDER BY threshold_xp,id",(interaction.guild_id,)); lines=[f"🏅 **{r['title']}** · {r['threshold_xp']} XP\n{r['description']}" for r in rows[:20]]; await interaction.response.send_message(embed=EmbedFactory.info(title="Achievements",description="\n".join(lines)))

    @app_commands.command(name="quote_add", description="Zitat speichern.")
    async def quote_add(self, interaction: discord.Interaction, text: str, autor: discord.Member | None = None) -> None:
        quote_id=await self.bot.database.execute("INSERT INTO quotes(guild_id,author_text,content,source_user_id,created_by) VALUES(?,?,?,?,?)",(interaction.guild_id,autor.display_name if autor else interaction.user.display_name,text.strip(),autor.id if autor else interaction.user.id,interaction.user.id)); await interaction.response.send_message(f"Zitat `#{quote_id}` gespeichert.",ephemeral=True)

    @app_commands.command(name="quote", description="Zufälliges oder bestimmtes Zitat anzeigen.")
    async def quote(self, interaction: discord.Interaction, quote_id: int | None = None) -> None:
        if quote_id is not None: row=await self.bot.database.fetchone("SELECT * FROM quotes WHERE guild_id=? AND id=?",(interaction.guild_id,quote_id))
        else: row=await self.bot.database.fetchone("SELECT * FROM quotes WHERE guild_id=? ORDER BY RANDOM() LIMIT 1",(interaction.guild_id,))
        if not row: await interaction.response.send_message("Kein Zitat gefunden.",ephemeral=True); return
        await interaction.response.send_message(embed=EmbedFactory.info(title=f"Zitat #{row['id']}",description=f"“{str(row['content'])[:3500]}”\n\n— **{row['author_text']}**"))

    @app_commands.command(name="giveaway_start", description="Giveaway mit Teilnahme-Button starten.")
    @app_commands.default_permissions(manage_messages=True)
    async def giveaway_start(self, interaction: discord.Interaction, preis: str, minuten: app_commands.Range[int,1,10080], gewinner: app_commands.Range[int,1,20]=1, kanal: discord.TextChannel | None = None) -> None:
        target=kanal or interaction.channel
        if not isinstance(target,discord.abc.Messageable): await interaction.response.send_message("Kein gültiger Zielkanal.",ephemeral=True); return
        ends=datetime.now(UTC)+timedelta(minutes=int(minuten)); giveaway_id=await self.bot.database.execute("INSERT INTO giveaways(guild_id,channel_id,prize,ends_at,winner_count,status,created_by) VALUES(?,?,?,?,?,'open',?)",(interaction.guild_id,target.id,preis.strip(),ends.isoformat(),int(gewinner),interaction.user.id)); view=GiveawayView(giveaway_id); embed=EmbedFactory.info(title="🎉 Giveaway",description=f"**{preis}**\n\nEnde: <t:{int(ends.timestamp())}:R>\nGewinner: **{gewinner}**\n\nMit dem Button teilnehmen."); msg=await target.send(embed=embed,view=view); await self.bot.database.execute("UPDATE giveaways SET message_id=? WHERE id=?",(msg.id,giveaway_id)); self.bot.add_view(view,message_id=msg.id); await interaction.response.send_message(f"Giveaway `#{giveaway_id}` gestartet.",ephemeral=True)

    @app_commands.command(name="giveaway_end", description="Giveaway sofort beenden.")
    @app_commands.default_permissions(manage_messages=True)
    async def giveaway_end(self, interaction: discord.Interaction, giveaway_id: int) -> None:
        row=await self.bot.database.fetchone("SELECT * FROM giveaways WHERE id=? AND guild_id=? AND status='open'",(giveaway_id,interaction.guild_id))
        if not row: await interaction.response.send_message("Offenes Giveaway nicht gefunden.",ephemeral=True); return
        winners=await self._finish_giveaway(row); await interaction.response.send_message(f"Beendet. Gewinner: {', '.join(f'<@{x}>' for x in winners) if winners else 'keine'}",ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CommunityPlus(bot))
