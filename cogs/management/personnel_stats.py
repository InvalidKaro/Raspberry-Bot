from __future__ import annotations
from io import BytesIO
from datetime import date
import discord
from discord import app_commands
from discord.ext import commands
from helpers.embeds import EmbedFactory
from services.personnel_v2 import PersonnelService
from services.personnel_export import render_personnel_png, render_personnel_chart

class Personnel(commands.GroupCog, group_name="perso", group_description="MD Personalabteilung • Mitarbeiter & Statistiken"):
    def __init__(self, bot):
        self.bot=bot
        self.service=PersonnelService(bot)

    async def _name_auto(self, interaction, current):
        if not interaction.guild_id: return []
        rows=await self.service.list_members(interaction.guild_id)
        cur=current.lower()
        return [app_commands.Choice(name=str(r["display_name"])[:100],value=str(r["display_name"])[:100])
                for r in rows if cur in str(r["display_name"]).lower()][:25]

    @app_commands.command(name="add", description="Mitarbeiter einmalig zur Perso-Datenbank hinzufügen.")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_messages=True)
    async def add(self, interaction:discord.Interaction, name:str, mitglied:discord.Member|None=None, rang:str|None=None, abteilung:str|None=None):
        row=await self.service.add(interaction.guild_id,name,interaction.user.id,user_id=mitglied.id if mitglied else None,rank=rang,department=abteilung)
        if hasattr(self.bot,"audit"): await self.bot.audit.record("perso.member.add",guild_id=interaction.guild_id,actor_id=interaction.user.id,target_type="personnel",target_id=row["id"],after=dict(row))
        await interaction.response.send_message(embed=EmbedFactory.success(title="Mitarbeiter gespeichert",description=f"**{row['display_name']}** ist jetzt in der Perso-Datenbank."),ephemeral=True)

    @app_commands.command(name="record", description="Einweisungen/BWG für eine gespeicherte Person eintragen.")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.autocomplete(person=_name_auto)
    async def record(self, interaction:discord.Interaction, person:str, einweisungen:app_commands.Range[int,0,999]=0, bwg:app_commands.Range[int,0,999]=0, zeitraum:str|None=None, datum:str|None=None, notiz:str|None=None):
        row=await self.service.get_by_name(interaction.guild_id,person)
        if not row:
            await interaction.response.send_message(embed=EmbedFactory.error(title="Nicht gefunden",description="Diese Person ist noch nicht gespeichert. Nutze zuerst `/perso add`."),ephemeral=True); return
        try:
            d=datum or date.today().isoformat(); date.fromisoformat(d)
        except ValueError:
            await interaction.response.send_message(embed=EmbedFactory.error(title="Datum ungültig",description="Bitte `YYYY-MM-DD` verwenden."),ephemeral=True); return
        rid=await self.service.record(interaction.guild_id,int(row["id"]),interaction.user.id,inductions=einweisungen,bwg=bwg,record_date=d,period_key=zeitraum,note=notiz)
        if hasattr(self.bot,"audit"): await self.bot.audit.record("perso.record.add",guild_id=interaction.guild_id,actor_id=interaction.user.id,target_type="personnel_record",target_id=rid,after={"person":row["display_name"],"einweisungen":einweisungen,"bwg":bwg,"datum":d,"zeitraum":zeitraum})
        await interaction.response.send_message(embed=EmbedFactory.success(title="Perso-Daten eingetragen",description=f"**{row['display_name']}**\nEinweisungen: **{einweisungen}** · BWG: **{bwg}**\nDatum: `{d}`"),ephemeral=True)

    @app_commands.command(name="overview", description="Gesamtübersicht aller gespeicherten Angestellten.")
    @app_commands.guild_only()
    async def overview(self, interaction:discord.Interaction, zeitraum:str|None=None):
        rows=await self.service.totals(interaction.guild_id,period_like=zeitraum)
        if not rows:
            await interaction.response.send_message(embed=EmbedFactory.info(title="Keine Perso-Daten",description="Noch keine Mitarbeiter gespeichert."),ephemeral=True); return
        lines=[f"**{i}. {r['display_name']}** — E **{r['inductions']}** · BWG **{r['bwg']}** · Gesamt **{r['activity']}**" for i,r in enumerate(rows[:20],1)]
        total_e=sum(int(r["inductions"]) for r in rows); total_b=sum(int(r["bwg"]) for r in rows)
        e=EmbedFactory.info(title="MD Perso • Übersicht",description="\n".join(lines))
        e.add_field(name="Gesamt",value=f"Einweisungen **{total_e}**\nBWG **{total_b}**",inline=True)
        e.add_field(name="Top Activity",value=f"**{rows[0]['display_name']}** · {rows[0]['activity']}",inline=True)
        e.add_field(name="Low Activity",value=f"**{rows[-1]['display_name']}** · {rows[-1]['activity']}",inline=True)
        await interaction.response.send_message(embed=e)

    @app_commands.command(name="leaderboard", description="Ranking nach Einweisungen, BWG oder Gesamtaktivität.")
    @app_commands.guild_only()
    @app_commands.choices(metric=[
        app_commands.Choice(name="Gesamt", value="activity"),
        app_commands.Choice(name="Einweisungen", value="inductions"),
        app_commands.Choice(name="BWG", value="bwg"),
    ])
    async def leaderboard(self, interaction:discord.Interaction, metric:app_commands.Choice[str], zeitraum:str|None=None):
        rows=await self.service.totals(interaction.guild_id,period_like=zeitraum)
        if not rows:
            await interaction.response.send_message(embed=EmbedFactory.info(title="Keine Perso-Daten",description="Für diesen Zeitraum sind keine Daten vorhanden."),ephemeral=True); return
        key=metric.value
        ordered=sorted(rows,key=lambda r:int(r[key]),reverse=True)
        labels={"activity":"Gesamt","inductions":"Einweisungen","bwg":"BWG"}
        medals=("🥇","🥈","🥉")
        lines=[]
        for i,r in enumerate(ordered[:15],1):
            prefix=medals[i-1] if i<=3 else f"`{i:02d}.`"
            lines.append(f"{prefix} **{r['display_name']}** — **{int(r[key])}**")
        title=f"Perso • Leaderboard • {labels[key]}"
        if zeitraum: title+=f" • {zeitraum}"
        await interaction.response.send_message(embed=EmbedFactory.info(title=title,description="\n".join(lines)))

    @app_commands.command(name="person", description="Historie und Summen einer einzelnen Person.")
    @app_commands.guild_only()
    @app_commands.autocomplete(person=_name_auto)
    async def person(self, interaction:discord.Interaction, person:str):
        row=await self.service.get_by_name(interaction.guild_id,person)
        if not row:
            await interaction.response.send_message(embed=EmbedFactory.error(title="Nicht gefunden",description="Person nicht in der Perso-Datenbank."),ephemeral=True); return
        history=await self.service.history(interaction.guild_id,int(row["id"]),30)
        te=sum(int(r["inductions"]) for r in history); tb=sum(int(r["bwg"]) for r in history)
        lines=[f"`{r['record_date']}` · E **{r['inductions']}** · BWG **{r['bwg']}**" + (f" · {r['note']}" if r["note"] else "") for r in history[:12]]
        e=EmbedFactory.info(title=f"Perso • {row['display_name']}",description="\n".join(lines) if lines else "Noch keine Einträge.")
        e.add_field(name="Gesamt",value=f"Einweisungen **{te}**\nBWG **{tb}**",inline=True)
        if row["rank_name"] or row["department"]: e.add_field(name="Profil",value=f"Rang: **{row['rank_name'] or '—'}**\nAbteilung: **{row['department'] or '—'}**",inline=True)
        await interaction.response.send_message(embed=e)

    @app_commands.command(name="compare", description="Wochen-/Monatsvergleich anhand des gespeicherten Zeitraum-Schlüssels.")
    @app_commands.guild_only()
    async def compare(self, interaction:discord.Interaction, zeitraum_a:str, zeitraum_b:str):
        a=await self.service.totals(interaction.guild_id,period_like=zeitraum_a)
        b=await self.service.totals(interaction.guild_id,period_like=zeitraum_b)
        ma={r["display_name"]:r for r in a}; mb={r["display_name"]:r for r in b}
        names=sorted(set(ma)|set(mb))
        lines=[]
        for n in names[:20]:
            av=int(ma.get(n,{"activity":0})["activity"]); bv=int(mb.get(n,{"activity":0})["activity"])
            delta=bv-av; sign="+" if delta>=0 else ""
            lines.append(f"**{n}** · {zeitraum_a}: {av} → {zeitraum_b}: {bv} (`{sign}{delta}`)")
        await interaction.response.send_message(embed=EmbedFactory.info(title="Perso • Vergleich",description="\n".join(lines) or "Keine Daten."))

    @app_commands.command(name="report", description="Kompletten Perso-Bericht mit Übersicht und Diagramm erstellen.")
    @app_commands.guild_only()
    async def report(self, interaction:discord.Interaction, zeitraum:str|None=None):
        await interaction.response.defer(ephemeral=True)
        rows=await self.service.totals(interaction.guild_id,period_like=zeitraum)
        if not rows:
            await interaction.followup.send(embed=EmbedFactory.info(title="Keine Perso-Daten",description="Für diesen Zeitraum sind keine Daten vorhanden."),ephemeral=True); return
        overview=render_personnel_png("MD Personalabteilung • Statistik",rows)
        chart=render_personnel_chart("MD Personalabteilung • Aktivitätsdiagramm",rows)
        files=[
            discord.File(BytesIO(overview),filename="perso-statistik.png"),
            discord.File(BytesIO(chart),filename="perso-diagramm.png"),
        ]
        total_e=sum(int(r["inductions"]) for r in rows)
        total_b=sum(int(r["bwg"]) for r in rows)
        top=max(rows,key=lambda r:int(r["activity"]))
        desc=f"**{len(rows)} Personen**\nEinweisungen: **{total_e}** · BWG: **{total_b}** · Gesamt: **{total_e+total_b}**\nTop: **{top['display_name']}** · {top['activity']}"
        if zeitraum: desc=f"Zeitraum: **{zeitraum}**\n"+desc
        await interaction.followup.send(embed=EmbedFactory.info(title="Perso • Bericht",description=desc),files=files,ephemeral=True)

    @app_commands.command(name="export", description="Perso-Daten als Übersicht, Diagramm oder CSV exportieren.")
    @app_commands.guild_only()
    @app_commands.choices(format=[
        app_commands.Choice(name="Übersicht PNG",value="png"),
        app_commands.Choice(name="Diagramm PNG",value="chart"),
        app_commands.Choice(name="CSV",value="csv"),
    ])
    async def export(self, interaction:discord.Interaction, format:app_commands.Choice[str], zeitraum:str|None=None):
        await interaction.response.defer(ephemeral=True)
        rows=await self.service.totals(interaction.guild_id,period_like=zeitraum)
        if format.value=="csv":
            data=self.service.csv_bytes(rows)
            file=discord.File(BytesIO(data),filename="perso-statistik.csv")
        elif format.value=="chart":
            data=render_personnel_chart("MD Personalabteilung • Aktivitätsdiagramm",rows)
            file=discord.File(BytesIO(data),filename="perso-diagramm.png")
        else:
            data=render_personnel_png("MD Personalabteilung • Statistik",rows)
            file=discord.File(BytesIO(data),filename="perso-statistik.png")
        await interaction.followup.send(file=file,ephemeral=True)

    @app_commands.command(name="stats", description="Kurzer Einstieg in das neue gespeicherte Perso-System.")
    async def stats(self, interaction:discord.Interaction):
        e=EmbedFactory.info(title="Perso 2.0",description="**1.** `/perso add` Person einmalig speichern\n**2.** `/perso record` Einweisungen/BWG eintragen\n**3.** `/perso overview` Gesamtübersicht\n**4.** `/perso leaderboard` Ranking\n**5.** `/perso person` Historie einer Person\n**6.** `/perso report` kompletter Bericht\n**7.** `/perso export` Übersicht/Diagramm/CSV")
        await interaction.response.send_message(embed=e,ephemeral=True)

async def setup(bot):
    await bot.add_cog(Personnel(bot))
