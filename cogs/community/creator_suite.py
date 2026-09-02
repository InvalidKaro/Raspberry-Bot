from __future__ import annotations

import json
from typing import Iterable

import discord
from discord import app_commands
from discord.ext import commands

from helpers.embeds import EmbedFactory


def _parse_color(value: str | None) -> discord.Color:
    if not value:
        return discord.Color.blurple()
    raw = value.strip().lower().removeprefix("#").removeprefix("0x")
    try:
        return discord.Color(int(raw, 16))
    except (ValueError, TypeError):
        return discord.Color.blurple()


class StoredFormModal(discord.ui.Modal):
    def __init__(self, bot: commands.Bot, form_row, questions: list[str]) -> None:
        super().__init__(title=str(form_row["title"])[:45], timeout=300)
        self.bot = bot; self.form_row = form_row; self.inputs: list[discord.ui.TextInput] = []
        for index, question in enumerate(questions[:5]):
            item = discord.ui.TextInput(label=question[:45], custom_id=f"q{index}", required=True, max_length=1000, style=discord.TextStyle.paragraph if len(question) > 30 else discord.TextStyle.short)
            self.inputs.append(item); self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        payload = [{"question": item.label, "answer": str(item.value)} for item in self.inputs]
        await self.bot.database.execute("INSERT INTO form_responses(form_id,guild_id,user_id,response_json) VALUES(?,?,?,?)", (int(self.form_row["id"]), interaction.guild_id, interaction.user.id, json.dumps(payload, ensure_ascii=False)))
        await interaction.response.send_message(embed=EmbedFactory.success(title="Formular gesendet", description=f"Deine Antwort für **{self.form_row['title']}** wurde gespeichert."), ephemeral=True)


class RoleButton(discord.ui.Button):
    def __init__(self, action_row) -> None:
        self.action_id = int(action_row["id"]); self.action_type = str(action_row["action_type"]); self.value = str(action_row["value"]); label = str(action_row["label"])[:80]
        if self.action_type == "link": super().__init__(label=label, style=discord.ButtonStyle.link, url=self.value)
        else: super().__init__(label=label, style=discord.ButtonStyle.secondary, custom_id=f"suite:panel:{self.action_id}")

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.action_type == "role":
            if not interaction.guild or not isinstance(interaction.user, discord.Member): await interaction.response.send_message("Nur auf einem Server nutzbar.", ephemeral=True); return
            try: role_id = int(self.value)
            except ValueError: await interaction.response.send_message("Ungültige Rollen-ID.", ephemeral=True); return
            role = interaction.guild.get_role(role_id)
            if not role: await interaction.response.send_message("Rolle wurde nicht gefunden.", ephemeral=True); return
            if role in interaction.user.roles: await interaction.user.remove_roles(role, reason="Creator role panel"); text = f"Rolle **{role.name}** entfernt."
            else: await interaction.user.add_roles(role, reason="Creator role panel"); text = f"Rolle **{role.name}** hinzugefügt."
            await interaction.response.send_message(text, ephemeral=True); return
        if self.action_type == "info": await interaction.response.send_message(self.value[:1900], ephemeral=True)


class RoleSelect(discord.ui.Select):
    def __init__(self, action_row) -> None:
        self.action_id = int(action_row["id"])
        try: data = json.loads(str(action_row["value"]))
        except json.JSONDecodeError: data = []
        options = [discord.SelectOption(label=str(item["label"])[:100], value=str(item["role_id"])) for item in data[:25]]
        super().__init__(placeholder=str(action_row["label"])[:100], min_values=1, max_values=1, options=options, custom_id=f"suite:roleselect:{self.action_id}")
        self.role_ids = [int(item["role_id"]) for item in data[:25] if str(item.get("role_id", "")).isdigit()]

    async def callback(self, interaction: discord.Interaction) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member): await interaction.response.send_message("Nur auf einem Server nutzbar.", ephemeral=True); return
        target_id = int(self.values[0]); target = interaction.guild.get_role(target_id)
        if not target: await interaction.response.send_message("Rolle wurde nicht gefunden.", ephemeral=True); return
        removable = [role for rid in self.role_ids if (role := interaction.guild.get_role(rid)) is not None and role in interaction.user.roles and rid != target_id]
        if removable: await interaction.user.remove_roles(*removable, reason="Creator role select")
        if target not in interaction.user.roles: await interaction.user.add_roles(target, reason="Creator role select")
        await interaction.response.send_message(f"Ausgewählt: **{target.name}**", ephemeral=True)


class StoredPanelView(discord.ui.View):
    def __init__(self, actions: Iterable) -> None:
        super().__init__(timeout=None)
        for action in actions: self.add_item(RoleSelect(action) if str(action["action_type"]) == "select-role" else RoleButton(action))


class CreatorSuite(commands.GroupCog, group_name="creator", group_description="Embeds, Ankündigungen, Vorlagen, Formulare und Panels"):
    def __init__(self, bot: commands.Bot) -> None: self.bot = bot

    async def cog_load(self) -> None:
        rows = await self.bot.database.fetchall("SELECT id,message_id FROM panel_messages WHERE message_id IS NOT NULL ORDER BY id DESC LIMIT 100")
        for panel in rows:
            actions = await self.bot.database.fetchall("SELECT * FROM panel_actions WHERE panel_id=? ORDER BY position,id", (panel["id"],))
            if actions: self.bot.add_view(StoredPanelView(actions), message_id=int(panel["message_id"]))

    @app_commands.command(name="announce", description="Ankündigung als sauberes Embed senden.")
    @app_commands.default_permissions(manage_messages=True)
    async def announce(self, interaction: discord.Interaction, titel: str, text: str, kanal: discord.TextChannel | None = None, farbe: str | None = None, bild_url: str | None = None, ping: discord.Role | None = None) -> None:
        target = kanal or interaction.channel
        if not isinstance(target, discord.abc.Messageable): await interaction.response.send_message("Kein gültiger Zielkanal.", ephemeral=True); return
        embed = discord.Embed(title=titel[:256], description=text[:4096], color=_parse_color(farbe))
        if bild_url: embed.set_image(url=bild_url)
        await target.send(content=ping.mention if ping else None, embed=embed, allowed_mentions=discord.AllowedMentions(roles=bool(ping), users=False, everyone=False)); await interaction.response.send_message("Ankündigung gesendet.", ephemeral=True)

    @app_commands.command(name="embed", description="Eigenes Embed direkt erstellen und senden.")
    @app_commands.default_permissions(manage_messages=True)
    async def embed_builder(self, interaction: discord.Interaction, titel: str, text: str, kanal: discord.TextChannel | None = None, farbe: str | None = None, footer: str | None = None, thumbnail: str | None = None) -> None:
        target = kanal or interaction.channel
        if not isinstance(target, discord.abc.Messageable): await interaction.response.send_message("Kein gültiger Zielkanal.", ephemeral=True); return
        embed = discord.Embed(title=titel[:256], description=text[:4096], color=_parse_color(farbe))
        if footer: embed.set_footer(text=footer[:2048])
        if thumbnail: embed.set_thumbnail(url=thumbnail)
        await target.send(embed=embed); await interaction.response.send_message("Embed gesendet.", ephemeral=True)

    @app_commands.command(name="template_save", description="Nachrichtenvorlage speichern/aktualisieren.")
    @app_commands.default_permissions(manage_messages=True)
    async def template_save(self, interaction: discord.Interaction, name: str, titel: str, text: str, farbe: str | None = None) -> None:
        await self.bot.database.execute("INSERT INTO content_templates(guild_id,name,title,body,color,created_by) VALUES(?,?,?,?,?,?) ON CONFLICT(guild_id,name) DO UPDATE SET title=excluded.title,body=excluded.body,color=excluded.color,created_by=excluded.created_by,updated_at=CURRENT_TIMESTAMP", (interaction.guild_id, name.strip().lower(), titel.strip(), text.strip(), _parse_color(farbe).value, interaction.user.id)); await interaction.response.send_message("Vorlage gespeichert.", ephemeral=True)

    @app_commands.command(name="template_send", description="Gespeicherte Vorlage senden.")
    @app_commands.default_permissions(manage_messages=True)
    async def template_send(self, interaction: discord.Interaction, name: str, kanal: discord.TextChannel | None = None) -> None:
        row = await self.bot.database.fetchone("SELECT * FROM content_templates WHERE guild_id=? AND lower(name)=lower(?)", (interaction.guild_id, name.strip()))
        if not row: await interaction.response.send_message("Vorlage nicht gefunden.", ephemeral=True); return
        target = kanal or interaction.channel
        if not isinstance(target, discord.abc.Messageable): await interaction.response.send_message("Kein gültiger Zielkanal.", ephemeral=True); return
        await target.send(embed=discord.Embed(title=str(row["title"])[:256], description=str(row["body"])[:4096], color=int(row["color"] or discord.Color.blurple().value))); await interaction.response.send_message("Vorlage gesendet.", ephemeral=True)

    @app_commands.command(name="template_list", description="Gespeicherte Vorlagen anzeigen.")
    async def template_list(self, interaction: discord.Interaction) -> None:
        rows = await self.bot.database.fetchall("SELECT name,title FROM content_templates WHERE guild_id=? ORDER BY name LIMIT 30", (interaction.guild_id,)); await interaction.response.send_message(embed=EmbedFactory.info(title="Vorlagen", description="\n".join(f"`{r['name']}` · **{r['title']}**" for r in rows) or "Noch keine Vorlagen."), ephemeral=True)

    @app_commands.command(name="form_create", description="Formular mit bis zu 5 Fragen erstellen.")
    @app_commands.default_permissions(manage_messages=True)
    async def form_create(self, interaction: discord.Interaction, name: str, titel: str, fragen: str) -> None:
        questions = [q.strip() for q in fragen.split("|") if q.strip()][:5]
        if not questions: await interaction.response.send_message("Fragen mit `|` trennen, z. B. `Name|Warum?|Erfahrung`.", ephemeral=True); return
        await self.bot.database.execute("INSERT INTO forms(guild_id,name,title,questions_json,created_by) VALUES(?,?,?,?,?) ON CONFLICT(guild_id,name) DO UPDATE SET title=excluded.title,questions_json=excluded.questions_json,created_by=excluded.created_by,updated_at=CURRENT_TIMESTAMP", (interaction.guild_id, name.strip().lower(), titel.strip(), json.dumps(questions, ensure_ascii=False), interaction.user.id)); await interaction.response.send_message(f"Formular **{titel}** mit {len(questions)} Fragen gespeichert.", ephemeral=True)

    @app_commands.command(name="form_open", description="Gespeichertes Formular öffnen.")
    async def form_open(self, interaction: discord.Interaction, name: str) -> None:
        row = await self.bot.database.fetchone("SELECT * FROM forms WHERE guild_id=? AND lower(name)=lower(?)", (interaction.guild_id, name.strip()))
        if not row: await interaction.response.send_message("Formular nicht gefunden.", ephemeral=True); return
        try: questions = json.loads(str(row["questions_json"]))
        except json.JSONDecodeError: await interaction.response.send_message("Formulardaten sind beschädigt.", ephemeral=True); return
        await interaction.response.send_modal(StoredFormModal(self.bot, row, questions))

    @app_commands.command(name="form_results", description="Letzte Formularantworten anzeigen.")
    @app_commands.default_permissions(manage_messages=True)
    async def form_results(self, interaction: discord.Interaction, name: str) -> None:
        form = await self.bot.database.fetchone("SELECT * FROM forms WHERE guild_id=? AND lower(name)=lower(?)", (interaction.guild_id, name.strip()))
        if not form: await interaction.response.send_message("Formular nicht gefunden.", ephemeral=True); return
        rows = await self.bot.database.fetchall("SELECT * FROM form_responses WHERE form_id=? ORDER BY created_at DESC,id DESC LIMIT 10", (form["id"],)); lines=[]
        for row in rows:
            try: answers=json.loads(str(row["response_json"])); preview=" · ".join(str(item.get("answer", ""))[:60] for item in answers[:2])
            except json.JSONDecodeError: preview="Ungültige Antwortdaten"
            lines.append(f"<@{row['user_id']}> · `{row['created_at']}` · {preview}")
        await interaction.response.send_message(embed=EmbedFactory.info(title=f"Formularantworten · {form['title']}", description="\n".join(lines) or "Noch keine Antworten."), ephemeral=True)

    @app_commands.command(name="panel_create", description="Button-Panel für Rollen, Infos und Links erstellen.")
    @app_commands.default_permissions(manage_roles=True)
    async def panel_create(self, interaction: discord.Interaction, titel: str, aktionen: str, kanal: discord.TextChannel | None = None) -> None:
        specs=[part.strip() for part in aktionen.split(";") if part.strip()][:5]; parsed=[]
        for spec in specs:
            parts=[x.strip() for x in spec.split("|",2)]
            if len(parts)!=3 or parts[1] not in {"role","link","info"}: continue
            if parts[1]=="role" and not parts[2].isdigit(): continue
            parsed.append((parts[0][:80],parts[1],parts[2]))
        if not parsed: await interaction.response.send_message("Format: `Label|role|ROLE_ID; Website|link|https://...; Info|info|Text`", ephemeral=True); return
        panel_id=await self.bot.database.execute("INSERT INTO panel_messages(guild_id,title,created_by) VALUES(?,?,?)",(interaction.guild_id,titel.strip(),interaction.user.id))
        for pos,(label,action_type,value) in enumerate(parsed): await self.bot.database.execute("INSERT INTO panel_actions(panel_id,guild_id,label,action_type,value,position) VALUES(?,?,?,?,?,?)",(panel_id,interaction.guild_id,label,action_type,value,pos))
        actions=await self.bot.database.fetchall("SELECT * FROM panel_actions WHERE panel_id=? ORDER BY position,id",(panel_id,)); target=kanal or interaction.channel
        if not isinstance(target, discord.abc.Messageable): await interaction.response.send_message("Kein gültiger Zielkanal.", ephemeral=True); return
        view=StoredPanelView(actions); msg=await target.send(embed=EmbedFactory.info(title=titel,description="Wähle eine Aktion:"),view=view); await self.bot.database.execute("UPDATE panel_messages SET channel_id=?,message_id=? WHERE id=?",(msg.channel.id,msg.id,panel_id)); self.bot.add_view(view,message_id=msg.id); await interaction.response.send_message("Panel erstellt.", ephemeral=True)

    @app_commands.command(name="roleselect_create", description="Rollen-Auswahlmenü erstellen.")
    @app_commands.default_permissions(manage_roles=True)
    async def roleselect_create(self, interaction: discord.Interaction, titel: str, rollen: str, kanal: discord.TextChannel | None = None) -> None:
        items=[]
        for raw in rollen.split(","):
            if ":" not in raw: continue
            label,rid=raw.rsplit(":",1)
            if rid.strip().isdigit(): items.append({"label":label.strip()[:100],"role_id":int(rid.strip())})
        items=items[:25]
        if not items: await interaction.response.send_message("Format: `Rolle A:123456789,Rolle B:987654321`", ephemeral=True); return
        panel_id=await self.bot.database.execute("INSERT INTO panel_messages(guild_id,title,created_by) VALUES(?,?,?)",(interaction.guild_id,titel.strip(),interaction.user.id)); await self.bot.database.execute("INSERT INTO panel_actions(panel_id,guild_id,label,action_type,value,position) VALUES(?,?,?,?,?,0)",(panel_id,interaction.guild_id,"Rolle auswählen","select-role",json.dumps(items,ensure_ascii=False))); actions=await self.bot.database.fetchall("SELECT * FROM panel_actions WHERE panel_id=? ORDER BY position,id",(panel_id,)); target=kanal or interaction.channel
        if not isinstance(target, discord.abc.Messageable): await interaction.response.send_message("Kein gültiger Zielkanal.",ephemeral=True); return
        view=StoredPanelView(actions); msg=await target.send(embed=EmbedFactory.info(title=titel,description="Wähle deine Rolle:"),view=view); await self.bot.database.execute("UPDATE panel_messages SET channel_id=?,message_id=? WHERE id=?",(msg.channel.id,msg.id,panel_id)); self.bot.add_view(view,message_id=msg.id); await interaction.response.send_message("Rollen-Auswahl erstellt.",ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CreatorSuite(bot))
