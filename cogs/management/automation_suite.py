from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from helpers.embeds import EmbedFactory


def _parse_when(value: str) -> datetime:
    raw=value.strip().replace("Z","+00:00"); dt=datetime.fromisoformat(raw)
    if dt.tzinfo is None: dt=dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _safe_extension(value: str) -> bool:
    return value.startswith("cogs.") and all(part.replace("_","").isalnum() for part in value.split("."))


def _safe_webhook_url(value: str) -> bool:
    parsed=urlparse(value.strip()); return parsed.scheme in {"http","https"} and bool(parsed.netloc)


class AutomationSuite(commands.GroupCog, group_name="automation", group_description="Custom Commands, Scheduler, Plugins und Webhook Hub"):
    def __init__(self, bot: commands.Bot) -> None: self.bot=bot; self._task: asyncio.Task | None=None
    async def cog_load(self) -> None: self._task=asyncio.create_task(self._scheduler_loop(),name="automation-suite-loop")
    async def cog_unload(self) -> None:
        if self._task: self._task.cancel()

    @commands.Cog.listener()
    async def on_message(self,message:discord.Message)->None:
        if not message.guild or message.author.bot or not message.content.startswith("!"): return
        name=message.content[1:].split(maxsplit=1)[0].strip().lower()
        if not name or len(name)>64: return
        row=await self.bot.database.fetchone("SELECT response FROM custom_commands WHERE guild_id=? AND lower(name)=lower(?) AND enabled=1 LIMIT 1",(message.guild.id,name))
        if row: await message.channel.send(str(row["response"])[:1900])

    async def _send_webhook(self,url:str,content:str)->None:
        timeout=aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url,json={"content":content[:1900]}) as response:
                if response.status>=400: raise RuntimeError(f"Webhook HTTP {response.status}: {(await response.text())[:200]}")

    async def _run_job(self,row)->None:
        payload=json.loads(str(row["payload_json"] or "{}")); kind=str(row["kind"])
        if kind=="message":
            channel=self.bot.get_channel(int(payload["channel_id"]));
            if not isinstance(channel,discord.abc.Messageable): raise RuntimeError("Channel not found")
            await channel.send(str(payload["content"])[:1900])
        elif kind=="template":
            template=await self.bot.database.fetchone("SELECT * FROM content_templates WHERE guild_id=? AND lower(name)=lower(?)",(row["guild_id"],str(payload["template"])))
            if not template: raise RuntimeError("Template not found")
            channel=self.bot.get_channel(int(payload["channel_id"]));
            if not isinstance(channel,discord.abc.Messageable): raise RuntimeError("Channel not found")
            await channel.send(embed=discord.Embed(title=str(template["title"])[:256],description=str(template["body"])[:4096],color=int(template["color"] or discord.Color.blurple().value)))
        elif kind=="webhook":
            endpoint=await self.bot.database.fetchone("SELECT url FROM webhook_endpoints WHERE guild_id=? AND lower(name)=lower(?) AND enabled=1",(row["guild_id"],str(payload["endpoint"])))
            if not endpoint: raise RuntimeError("Webhook endpoint not found")
            await self._send_webhook(str(endpoint["url"]),str(payload["content"]))
        else: raise RuntimeError(f"Unsupported job kind: {kind}")

    async def _scheduler_loop(self)->None:
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            now=datetime.now(UTC); rows=await self.bot.database.fetchall("SELECT * FROM automation_jobs WHERE enabled=1 AND run_at<=? ORDER BY run_at,id LIMIT 10",(now.isoformat(),))
            for row in rows:
                status="ok"; error=None
                try: await self._run_job(row)
                except Exception as exc: status="error"; error=f"{type(exc).__name__}: {exc}"[:500]
                repeat=int(row["repeat_minutes"] or 0)
                if repeat>0:
                    try: base=_parse_when(str(row["run_at"]))
                    except ValueError: base=now
                    next_run=base
                    while next_run<=now: next_run+=timedelta(minutes=repeat)
                    await self.bot.database.execute("UPDATE automation_jobs SET run_at=?,last_run_at=CURRENT_TIMESTAMP,last_status=?,last_error=? WHERE id=?",(next_run.isoformat(),status,error,row["id"]))
                else: await self.bot.database.execute("UPDATE automation_jobs SET enabled=0,last_run_at=CURRENT_TIMESTAMP,last_status=?,last_error=? WHERE id=?",(status,error,row["id"]))
            await asyncio.sleep(15)

    @app_commands.command(name="custom_create", description="Eigenen !Custom-Command erstellen.")
    @app_commands.default_permissions(manage_messages=True)
    async def custom_create(self,interaction:discord.Interaction,name:str,antwort:str)->None:
        key=name.strip().lower().removeprefix("!")
        if not key.replace("_","").replace("-","").isalnum(): await interaction.response.send_message("Ungültiger Command-Name.",ephemeral=True); return
        await self.bot.database.execute("INSERT INTO custom_commands(guild_id,name,response,created_by) VALUES(?,?,?,?) ON CONFLICT(guild_id,name) DO UPDATE SET response=excluded.response,created_by=excluded.created_by,enabled=1,updated_at=CURRENT_TIMESTAMP",(interaction.guild_id,key,antwort.strip(),interaction.user.id)); await interaction.response.send_message(f"Custom Command **!{key}** gespeichert.",ephemeral=True)

    @app_commands.command(name="custom_run", description="Custom Command per Slash ausführen.")
    async def custom_run(self,interaction:discord.Interaction,name:str)->None:
        row=await self.bot.database.fetchone("SELECT response FROM custom_commands WHERE guild_id=? AND lower(name)=lower(?) AND enabled=1",(interaction.guild_id,name.strip().removeprefix("!")))
        if not row: await interaction.response.send_message("Custom Command nicht gefunden.",ephemeral=True); return
        await interaction.response.send_message(str(row["response"])[:1900])

    @app_commands.command(name="custom_list", description="Eigene Custom Commands anzeigen.")
    async def custom_list(self,interaction:discord.Interaction)->None:
        rows=await self.bot.database.fetchall("SELECT name,enabled FROM custom_commands WHERE guild_id=? ORDER BY name LIMIT 40",(interaction.guild_id,)); await interaction.response.send_message(embed=EmbedFactory.info(title="Custom Commands",description="\n".join(f"{'✅' if int(r['enabled']) else '⛔'} `!{r['name']}`" for r in rows) or "Noch keine Custom Commands."),ephemeral=True)

    @app_commands.command(name="schedule_message", description="Discord-Nachricht zeitgesteuert senden.")
    @app_commands.default_permissions(manage_messages=True)
    async def schedule_message(self,interaction:discord.Interaction,kanal:discord.TextChannel,zeitpunkt:str,text:str,wiederholen_minuten:app_commands.Range[int,0,10080]=0)->None:
        try: when=_parse_when(zeitpunkt)
        except ValueError: await interaction.response.send_message("Zeitpunkt ISO, z. B. `2026-09-05T20:00`.",ephemeral=True); return
        job_id=await self.bot.database.execute("INSERT INTO automation_jobs(guild_id,kind,payload_json,run_at,repeat_minutes,created_by) VALUES(?,?,?,?,?,?)",(interaction.guild_id,"message",json.dumps({"channel_id":str(kanal.id),"content":text},ensure_ascii=False),when.isoformat(),int(wiederholen_minuten),interaction.user.id)); await interaction.response.send_message(f"Scheduler-Job `#{job_id}` angelegt · <t:{int(when.timestamp())}:F>.",ephemeral=True)

    @app_commands.command(name="schedule_template", description="Vorlage zeitgesteuert senden.")
    @app_commands.default_permissions(manage_messages=True)
    async def schedule_template(self,interaction:discord.Interaction,vorlage:str,kanal:discord.TextChannel,zeitpunkt:str,wiederholen_minuten:app_commands.Range[int,0,10080]=0)->None:
        try: when=_parse_when(zeitpunkt)
        except ValueError: await interaction.response.send_message("Ungültiger Zeitpunkt.",ephemeral=True); return
        template=await self.bot.database.fetchone("SELECT id FROM content_templates WHERE guild_id=? AND lower(name)=lower(?)",(interaction.guild_id,vorlage))
        if not template: await interaction.response.send_message("Vorlage nicht gefunden.",ephemeral=True); return
        job_id=await self.bot.database.execute("INSERT INTO automation_jobs(guild_id,kind,payload_json,run_at,repeat_minutes,created_by) VALUES(?,?,?,?,?,?)",(interaction.guild_id,"template",json.dumps({"channel_id":str(kanal.id),"template":vorlage},ensure_ascii=False),when.isoformat(),int(wiederholen_minuten),interaction.user.id)); await interaction.response.send_message(f"Template-Job `#{job_id}` gespeichert.",ephemeral=True)

    @app_commands.command(name="schedule_webhook", description="Webhook-Nachricht zeitgesteuert senden.")
    @app_commands.default_permissions(manage_messages=True)
    async def schedule_webhook(self,interaction:discord.Interaction,endpoint:str,zeitpunkt:str,text:str,wiederholen_minuten:app_commands.Range[int,0,10080]=0)->None:
        try: when=_parse_when(zeitpunkt)
        except ValueError: await interaction.response.send_message("Ungültiger Zeitpunkt.",ephemeral=True); return
        row=await self.bot.database.fetchone("SELECT id FROM webhook_endpoints WHERE guild_id=? AND lower(name)=lower(?) AND enabled=1",(interaction.guild_id,endpoint.strip()))
        if not row: await interaction.response.send_message("Webhook-Endpunkt nicht gefunden.",ephemeral=True); return
        job_id=await self.bot.database.execute("INSERT INTO automation_jobs(guild_id,kind,payload_json,run_at,repeat_minutes,created_by) VALUES(?,?,?,?,?,?)",(interaction.guild_id,"webhook",json.dumps({"endpoint":endpoint.strip(),"content":text},ensure_ascii=False),when.isoformat(),int(wiederholen_minuten),interaction.user.id)); await interaction.response.send_message(f"Webhook-Job `#{job_id}` gespeichert.",ephemeral=True)

    @app_commands.command(name="schedule_list", description="Aktive Scheduler-Jobs anzeigen.")
    async def schedule_list(self,interaction:discord.Interaction)->None:
        rows=await self.bot.database.fetchall("SELECT id,kind,run_at,repeat_minutes,last_status FROM automation_jobs WHERE guild_id=? AND enabled=1 ORDER BY run_at LIMIT 30",(interaction.guild_id,)); lines=[f"`#{r['id']}` **{r['kind']}** · `{r['run_at']}` · repeat {r['repeat_minutes'] or 0}m · {r['last_status'] or '—'}" for r in rows]; await interaction.response.send_message(embed=EmbedFactory.info(title="Command Scheduler",description="\n".join(lines) or "Keine aktiven Jobs."),ephemeral=True)

    @app_commands.command(name="schedule_cancel", description="Scheduler-Job deaktivieren.")
    @app_commands.default_permissions(manage_messages=True)
    async def schedule_cancel(self,interaction:discord.Interaction,job_id:int)->None:
        await self.bot.database.execute("UPDATE automation_jobs SET enabled=0 WHERE id=? AND guild_id=?",(job_id,interaction.guild_id)); await interaction.response.send_message(f"Job `#{job_id}` deaktiviert.",ephemeral=True)

    @app_commands.command(name="webhook_add", description="Webhook-Endpunkt im Hub speichern.")
    @app_commands.default_permissions(manage_guild=True)
    async def webhook_add(self,interaction:discord.Interaction,name:str,url:str)->None:
        if not _safe_webhook_url(url): await interaction.response.send_message("Nur gültige HTTP(S)-URLs.",ephemeral=True); return
        await self.bot.database.execute("INSERT INTO webhook_endpoints(guild_id,name,url,created_by) VALUES(?,?,?,?) ON CONFLICT(guild_id,name) DO UPDATE SET url=excluded.url,enabled=1,created_by=excluded.created_by,updated_at=CURRENT_TIMESTAMP",(interaction.guild_id,name.strip().lower(),url.strip(),interaction.user.id)); await interaction.response.send_message("Webhook gespeichert.",ephemeral=True)

    @app_commands.command(name="webhook_send", description="Nachricht über gespeicherten Webhook senden.")
    @app_commands.default_permissions(manage_messages=True)
    async def webhook_send(self,interaction:discord.Interaction,name:str,text:str)->None:
        row=await self.bot.database.fetchone("SELECT url FROM webhook_endpoints WHERE guild_id=? AND lower(name)=lower(?) AND enabled=1",(interaction.guild_id,name.strip()))
        if not row: await interaction.response.send_message("Webhook nicht gefunden.",ephemeral=True); return
        await interaction.response.defer(ephemeral=True)
        try: await self._send_webhook(str(row["url"]),text)
        except Exception as exc: await interaction.followup.send(f"Fehler: `{type(exc).__name__}: {exc}`",ephemeral=True); return
        await interaction.followup.send("Webhook gesendet.",ephemeral=True)

    @app_commands.command(name="webhook_list", description="Webhook Hub anzeigen.")
    @app_commands.default_permissions(manage_guild=True)
    async def webhook_list(self,interaction:discord.Interaction)->None:
        rows=await self.bot.database.fetchall("SELECT name,enabled FROM webhook_endpoints WHERE guild_id=? ORDER BY name LIMIT 30",(interaction.guild_id,)); await interaction.response.send_message(embed=EmbedFactory.info(title="Webhook Hub",description="\n".join(f"{'✅' if int(r['enabled']) else '⛔'} `{r['name']}`" for r in rows) or "Noch keine Webhooks."),ephemeral=True)

    @app_commands.command(name="plugin_list", description="Geladene/konfigurierte Bot-Plugins anzeigen.")
    @app_commands.default_permissions(administrator=True)
    async def plugin_list(self,interaction:discord.Interaction)->None:
        states=await self.bot.database.fetchall("SELECT extension,enabled FROM plugin_state ORDER BY extension"); state_map={str(r["extension"]):bool(r["enabled"]) for r in states}; names=sorted(set(self.bot.extensions)|set(state_map)); lines=[f"{'✅' if state_map.get(name,name in self.bot.extensions) else '⛔'} `{name}`" for name in names if name.startswith("cogs.")]; await interaction.response.send_message(embed=EmbedFactory.info(title="Plugin System",description="\n".join(lines[:40]) or "Keine Plugins gefunden."),ephemeral=True)

    @app_commands.command(name="plugin_toggle", description="Cog-Plugin aktivieren/deaktivieren.")
    @app_commands.default_permissions(administrator=True)
    async def plugin_toggle(self,interaction:discord.Interaction,extension:str,aktiv:bool)->None:
        ext=extension.strip()
        if not _safe_extension(ext): await interaction.response.send_message("Nur `cogs.*` Extensions erlaubt.",ephemeral=True); return
        if ext=="cogs.management.automation_suite" and not aktiv: await interaction.response.send_message("Automation Suite kann sich nicht selbst deaktivieren.",ephemeral=True); return
        await self.bot.database.execute("INSERT INTO plugin_state(extension,enabled,updated_by) VALUES(?,?,?) ON CONFLICT(extension) DO UPDATE SET enabled=excluded.enabled,updated_by=excluded.updated_by,updated_at=CURRENT_TIMESTAMP",(ext,int(aktiv),interaction.user.id))
        try:
            if aktiv and ext not in self.bot.extensions: await self.bot.load_extension(ext)
            elif not aktiv and ext in self.bot.extensions: await self.bot.unload_extension(ext)
        except Exception as exc: await interaction.response.send_message(f"Status gespeichert, Live-Aktion fehlgeschlagen: `{type(exc).__name__}: {exc}`",ephemeral=True); return
        await interaction.response.send_message(f"`{ext}` → **{'aktiv' if aktiv else 'deaktiviert'}**.",ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AutomationSuite(bot))
