from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import discord
from discord.ext import commands, tasks

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS dashboard_activity(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    detail TEXT,
    actor_id INTEGER,
    target_id TEXT,
    source TEXT,
    metadata_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_dashboard_activity_guild_time ON dashboard_activity(guild_id,created_at);
CREATE INDEX IF NOT EXISTS idx_dashboard_activity_kind_time ON dashboard_activity(kind,created_at);

CREATE TABLE IF NOT EXISTS dashboard_error_events(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER,
    source TEXT NOT NULL,
    error_type TEXT NOT NULL,
    message TEXT,
    traceback TEXT,
    command_name TEXT,
    commit_sha TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_dashboard_errors_time ON dashboard_error_events(created_at);
CREATE INDEX IF NOT EXISTS idx_dashboard_errors_type_time ON dashboard_error_events(error_type,created_at);

CREATE TABLE IF NOT EXISTS dashboard_runtime_state(
    guild_id INTEGER PRIMARY KEY,
    state_json TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS dashboard_notification_rules(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    metric TEXT NOT NULL,
    operator TEXT NOT NULL DEFAULT '>',
    threshold REAL NOT NULL,
    duration_seconds INTEGER NOT NULL DEFAULT 0,
    cooldown_seconds INTEGER NOT NULL DEFAULT 1800,
    channel_id INTEGER NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    last_fired_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_dashboard_notification_rules_guild ON dashboard_notification_rules(guild_id,enabled);

CREATE TABLE IF NOT EXISTS dashboard_workflows(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    trigger_json TEXT NOT NULL,
    steps_json TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_dashboard_workflows_guild ON dashboard_workflows(guild_id,enabled);

CREATE TABLE IF NOT EXISTS dashboard_workflow_state(
    workflow_id INTEGER PRIMARY KEY,
    cursor_json TEXT,
    last_run_at TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(workflow_id) REFERENCES dashboard_workflows(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS dashboard_workflow_runs(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workflow_id INTEGER NOT NULL,
    guild_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    detail TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_dashboard_workflow_runs_time ON dashboard_workflow_runs(workflow_id,created_at);

CREATE TABLE IF NOT EXISTS dashboard_scheduled_messages(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    send_at TEXT NOT NULL,
    content TEXT,
    embed_json TEXT,
    buttons_json TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    result TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    processed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_dashboard_scheduled_due ON dashboard_scheduled_messages(status,send_at);
"""


def _json(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        parsed = json.loads(str(value or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback
    return parsed if isinstance(parsed, type(fallback)) else fallback


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _parse_time(value: object) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _compare(value: float, operator: str, threshold: float) -> bool:
    op = operator.strip()
    if op == ">":
        return value > threshold
    if op == ">=":
        return value >= threshold
    if op == "<":
        return value < threshold
    if op == "<=":
        return value <= threshold
    if op in {"=", "=="}:
        return value == threshold
    if op in {"!=", "<>"}:
        return value != threshold
    return False


def _fmt_template(value: object, context: dict[str, Any]) -> str:
    text = str(value or "")
    for key, replacement in context.items():
        text = text.replace("{" + key + "}", str(replacement if replacement is not None else ""))
    return text


class DashboardTelemetry(commands.Cog):
    """Low-frequency telemetry used only by Dashboard Pro.

    The loops are intentionally conservative for a Raspberry Pi 3: runtime state
    is written only when it changes, notification checks run once a minute and
    workflows run every 30 seconds.
    """

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._runtime_cache: dict[int, str] = {}
        self._condition_since: dict[int, float] = {}
        self.runtime_loop.start()
        self.notification_loop.start()
        self.workflow_loop.start()
        self.cleanup_loop.start()

    async def cog_load(self) -> None:
        await self.bot.database.connection.executescript(SCHEMA)
        await self.bot.database.connection.commit()

    async def cog_unload(self) -> None:
        self.runtime_loop.cancel()
        self.notification_loop.cancel()
        self.workflow_loop.cancel()
        self.cleanup_loop.cancel()

    async def activity(
        self,
        guild_id: int | None,
        kind: str,
        title: str,
        *,
        detail: str = "",
        actor_id: int | None = None,
        target_id: object | None = None,
        source: str = "discord",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        try:
            await self.bot.database.execute(
                """INSERT INTO dashboard_activity
                (guild_id,kind,title,detail,actor_id,target_id,source,metadata_json)
                VALUES(?,?,?,?,?,?,?,?)""",
                (
                    guild_id,
                    kind[:48],
                    title[:180],
                    detail[:1200],
                    actor_id,
                    str(target_id)[:120] if target_id is not None else None,
                    source[:100],
                    json.dumps(metadata or {}, ensure_ascii=False, separators=(",", ":")),
                ),
            )
        except Exception:
            logger.debug("Dashboard activity insert failed", exc_info=True)

    @commands.Cog.listener()
    async def on_app_command_completion(self, interaction: discord.Interaction, command) -> None:
        await self.activity(
            interaction.guild_id,
            "command",
            f"/{getattr(command, 'qualified_name', getattr(command, 'name', 'command'))}",
            detail=f"ausgeführt von {interaction.user}",
            actor_id=interaction.user.id,
            source=str(getattr(getattr(command, "callback", None), "__module__", "discord")),
        )

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        await self.activity(member.guild.id, "member_join", "Mitglied beigetreten", detail=str(member), actor_id=member.id, target_id=member.id)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        await self.activity(member.guild.id, "member_leave", "Mitglied verlassen", detail=str(member), actor_id=member.id, target_id=member.id)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState) -> None:
        if before.channel == after.channel:
            return
        if before.channel is None and after.channel is not None:
            title = "Voice beigetreten"
            detail = after.channel.name
            kind = "voice_join"
        elif before.channel is not None and after.channel is None:
            title = "Voice verlassen"
            detail = before.channel.name
            kind = "voice_leave"
        else:
            title = "Voice gewechselt"
            detail = f"{before.channel.name if before.channel else '—'} → {after.channel.name if after.channel else '—'}"
            kind = "voice_move"
        await self.activity(member.guild.id, kind, title, detail=detail, actor_id=member.id, target_id=member.id)

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel) -> None:
        await self.activity(channel.guild.id, "channel_create", "Channel erstellt", detail=channel.name, target_id=channel.id)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        await self.activity(channel.guild.id, "channel_delete", "Channel gelöscht", detail=channel.name, target_id=channel.id)

    @commands.Cog.listener()
    async def on_guild_channel_update(self, before: discord.abc.GuildChannel, after: discord.abc.GuildChannel) -> None:
        if before.name != after.name:
            await self.activity(after.guild.id, "channel_update", "Channel umbenannt", detail=f"{before.name} → {after.name}", target_id=after.id)

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role) -> None:
        await self.activity(role.guild.id, "role_create", "Rolle erstellt", detail=role.name, target_id=role.id)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role) -> None:
        await self.activity(role.guild.id, "role_delete", "Rolle gelöscht", detail=role.name, target_id=role.id)

    @commands.Cog.listener()
    async def on_guild_role_update(self, before: discord.Role, after: discord.Role) -> None:
        if before.name != after.name or before.permissions != after.permissions:
            await self.activity(after.guild.id, "role_update", "Rolle geändert", detail=f"{before.name} → {after.name}", target_id=after.id)

    def _runtime_payload(self, guild: discord.Guild) -> dict[str, Any]:
        voice_cog = self.bot.get_cog("VoiceSuite")
        yt_cog = self.bot.get_cog("YouTubeSuite")
        voice = guild.voice_client
        voice_state = getattr(voice_cog, "states", {}).get(guild.id) if voice_cog else None
        current_yt = getattr(yt_cog, "current", {}).get(guild.id) if yt_cog else None
        queue = list(getattr(yt_cog, "queues", {}).get(guild.id, ())) if yt_cog else []
        data: dict[str, Any] = {
            "voice": {
                "connected": bool(voice and voice.is_connected()),
                "channel_id": str(voice.channel.id) if voice and getattr(voice, "channel", None) else None,
                "channel_name": voice.channel.name if voice and getattr(voice, "channel", None) else None,
                "playing": bool(voice and voice.is_playing()),
                "paused": bool(voice and voice.is_paused()),
                "title": getattr(voice_state, "title", None),
                "kind": getattr(voice_state, "kind", None),
                "volume": getattr(voice_state, "volume", None),
                "source_name": getattr(voice_state, "source_name", None),
                "elapsed_seconds": max(0, int(time.monotonic() - voice_state.started_at)) if voice_state else 0,
            },
            "youtube": {
                "active": guild.id in getattr(yt_cog, "session_active", set()) if yt_cog else False,
                "current": {
                    "title": getattr(current_yt, "title", None),
                    "url": getattr(current_yt, "webpage_url", None),
                    "duration": getattr(current_yt, "duration", None),
                    "requested_by": getattr(current_yt, "requested_by", None),
                } if current_yt else None,
                "queue": [
                    {
                        "title": getattr(item, "title", "Song"),
                        "url": getattr(item, "webpage_url", ""),
                        "duration": getattr(item, "duration", None),
                        "requested_by": getattr(item, "requested_by", None),
                        "query": getattr(item, "query", ""),
                    }
                    for item in queue[:25]
                ],
            },
        }
        return data

    @tasks.loop(seconds=10)
    async def runtime_loop(self) -> None:
        for guild in self.bot.guilds:
            payload = self._runtime_payload(guild)
            encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            if self._runtime_cache.get(guild.id) == encoded:
                continue
            self._runtime_cache[guild.id] = encoded
            await self.bot.database.execute(
                """INSERT INTO dashboard_runtime_state(guild_id,state_json,updated_at)
                VALUES(?,?,CURRENT_TIMESTAMP)
                ON CONFLICT(guild_id) DO UPDATE SET state_json=excluded.state_json,updated_at=CURRENT_TIMESTAMP""",
                (guild.id, encoded),
            )

    @runtime_loop.before_loop
    async def before_runtime_loop(self) -> None:
        await self.bot.wait_until_ready()

    async def _metric_value(self, guild_id: int, metric: str) -> float | None:
        key = metric.strip().lower()
        if key in {"cpu", "ram", "temperature", "temp", "disk", "load"}:
            snap = await self.bot.system_metrics.get()
            mapping = {
                "cpu": snap.cpu_percent,
                "ram": snap.ram_percent,
                "temperature": snap.temperature,
                "temp": snap.temperature,
                "disk": snap.disk_percent,
                "load": snap.load_1m,
            }
            value = mapping.get(key)
            return float(value) if value is not None else None
        if key == "command_errors_5m":
            row = await self.bot.database.fetchone(
                "SELECT COUNT(*) count FROM command_analytics WHERE guild_id=? AND success=0 AND created_at>=datetime('now','-5 minutes')",
                (guild_id,),
            )
            return float(row["count"] if row else 0)
        if key == "open_tickets":
            row = await self.bot.database.fetchone(
                "SELECT COUNT(*) count FROM tickets WHERE guild_id=? AND status!='closed'",
                (guild_id,),
            )
            return float(row["count"] if row else 0)
        if key == "voice_sessions":
            return float(sum(1 for item in self.bot.voice_clients if item.is_connected()))
        return None

    @tasks.loop(seconds=60)
    async def notification_loop(self) -> None:
        rows = await self.bot.database.fetchall("SELECT * FROM dashboard_notification_rules WHERE enabled=1")
        now_mono = time.monotonic()
        now = datetime.now(UTC)
        for row in rows:
            rule_id = int(row["id"])
            try:
                value = await self._metric_value(int(row["guild_id"]), str(row["metric"]))
                if value is None or not _compare(value, str(row["operator"]), float(row["threshold"])):
                    self._condition_since.pop(rule_id, None)
                    continue
                since = self._condition_since.setdefault(rule_id, now_mono)
                if now_mono - since < max(0, int(row["duration_seconds"] or 0)):
                    continue
                last_fired = _parse_time(row["last_fired_at"])
                cooldown = max(60, int(row["cooldown_seconds"] or 1800))
                if last_fired and (now - last_fired).total_seconds() < cooldown:
                    continue
                channel = self.bot.get_channel(int(row["channel_id"]))
                if not isinstance(channel, discord.abc.Messageable):
                    continue
                embed = discord.Embed(
                    title=f"🔔 {row['name']}",
                    description=(
                        f"**{row['metric']}** ist aktuell **{value:.1f}**.\n"
                        f"Regel: `{row['metric']} {row['operator']} {row['threshold']}`"
                    ),
                    color=0xF59E0B,
                    timestamp=now,
                )
                await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
                await self.bot.database.execute(
                    "UPDATE dashboard_notification_rules SET last_fired_at=CURRENT_TIMESTAMP WHERE id=?",
                    (rule_id,),
                )
                await self.activity(int(row["guild_id"]), "notification", str(row["name"]), detail=f"{row['metric']}={value:.1f}", source="dashboard.notification")
            except Exception:
                logger.warning("Dashboard notification rule %s failed", rule_id, exc_info=True)

    @notification_loop.before_loop
    async def before_notification_loop(self) -> None:
        await self.bot.wait_until_ready()

    async def _workflow_contexts(self, row, trigger: dict[str, Any], state: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        guild_id = int(row["guild_id"])
        kind = str(trigger.get("type", "interval")).lower()
        contexts: list[dict[str, Any]] = []
        new_state = dict(state)
        if kind == "interval":
            minutes = max(1, min(10080, int(trigger.get("minutes", 60) or 60)))
            last_run = _parse_time(state.get("last_run"))
            if last_run is None or datetime.now(UTC) - last_run >= timedelta(minutes=minutes):
                contexts.append({"guild_id": guild_id})
                new_state["last_run"] = _now_iso()
        elif kind == "metric":
            metric = str(trigger.get("metric", "ram"))
            value = await self._metric_value(guild_id, metric)
            threshold = float(trigger.get("threshold", 85))
            operator = str(trigger.get("operator", ">"))
            if value is not None and _compare(value, operator, threshold):
                last_run = _parse_time(state.get("last_run"))
                cooldown = max(1, int(trigger.get("cooldown_minutes", 30)))
                if last_run is None or datetime.now(UTC) - last_run >= timedelta(minutes=cooldown):
                    contexts.append({"guild_id": guild_id, "metric": metric, "value": value})
                    new_state["last_run"] = _now_iso()
        elif kind == "ticket_created":
            cursor = int(state.get("cursor", 0) or 0)
            if cursor <= 0:
                latest = await self.bot.database.fetchone("SELECT COALESCE(MAX(id),0) max_id FROM tickets WHERE guild_id=?", (guild_id,))
                new_state["cursor"] = int(latest["max_id"] if latest else 0)
            else:
                rows = await self.bot.database.fetchall(
                    "SELECT id,opener_id,subject,category_name FROM tickets WHERE guild_id=? AND id>? ORDER BY id LIMIT 20",
                    (guild_id, cursor),
                )
                for item in rows:
                    contexts.append({"guild_id": guild_id, "ticket_id": item["id"], "user_id": item["opener_id"], "subject": item["subject"]})
                    new_state["cursor"] = max(int(new_state.get("cursor", 0)), int(item["id"]))
        elif kind == "form_response":
            cursor = int(state.get("cursor", 0) or 0)
            form_id = int(trigger.get("form_id", 0) or 0)
            where = "guild_id=? AND id>?"
            params: list[Any] = [guild_id, cursor]
            if form_id:
                where += " AND form_id=?"
                params.append(form_id)
            if cursor <= 0:
                latest = await self.bot.database.fetchone(f"SELECT COALESCE(MAX(id),0) max_id FROM form_responses WHERE {where.replace(' AND id>?','')}", tuple(params[:1] + ([form_id] if form_id else [])))
                new_state["cursor"] = int(latest["max_id"] if latest else 0)
            else:
                rows = await self.bot.database.fetchall(
                    f"SELECT id,form_id,user_id FROM form_responses WHERE {where} ORDER BY id LIMIT 20",
                    tuple(params),
                )
                for item in rows:
                    contexts.append({"guild_id": guild_id, "form_response_id": item["id"], "form_id": item["form_id"], "user_id": item["user_id"]})
                    new_state["cursor"] = max(int(new_state.get("cursor", 0)), int(item["id"]))
        elif kind == "activity":
            cursor = int(state.get("cursor", 0) or 0)
            activity_kind = str(trigger.get("kind", "member_join"))[:48]
            if cursor <= 0:
                latest = await self.bot.database.fetchone(
                    "SELECT COALESCE(MAX(id),0) max_id FROM dashboard_activity WHERE guild_id=? AND kind=?",
                    (guild_id, activity_kind),
                )
                new_state["cursor"] = int(latest["max_id"] if latest else 0)
            else:
                rows = await self.bot.database.fetchall(
                    "SELECT id,actor_id,target_id,title,detail FROM dashboard_activity WHERE guild_id=? AND kind=? AND id>? ORDER BY id LIMIT 20",
                    (guild_id, activity_kind, cursor),
                )
                for item in rows:
                    contexts.append({"guild_id": guild_id, "activity_id": item["id"], "user_id": item["actor_id"] or item["target_id"], "title": item["title"], "detail": item["detail"]})
                    new_state["cursor"] = max(int(new_state.get("cursor", 0)), int(item["id"]))
        return contexts, new_state

    async def _run_step(self, guild: discord.Guild, step: dict[str, Any], context: dict[str, Any]) -> str:
        kind = str(step.get("type", "log")).lower()
        if kind == "send_message":
            channel_id = int(_fmt_template(step.get("channel_id"), context) or 0)
            channel = guild.get_channel(channel_id)
            if not isinstance(channel, discord.abc.Messageable):
                raise ValueError("Workflow channel not found")
            text = _fmt_template(step.get("text", "Workflow"), context)[:1900]
            await channel.send(text, allowed_mentions=discord.AllowedMentions.none())
            return f"message->{channel_id}"
        if kind == "create_task":
            title = _fmt_template(step.get("title", "Workflow task"), context)[:180]
            details = _fmt_template(step.get("details", ""), context)[:2000]
            assigned_raw = _fmt_template(step.get("assigned_to", context.get("user_id", "")), context)
            assigned = int(assigned_raw) if assigned_raw.isdigit() else None
            await self.bot.database.execute(
                "INSERT INTO workspace_tasks(guild_id,title,details,status,assigned_to,created_by) VALUES(?,?,?,'open',?,0)",
                (guild.id, title, details, assigned),
            )
            return "task-created"
        if kind == "add_role":
            user_raw = _fmt_template(step.get("user_id", context.get("user_id", "")), context)
            role_raw = _fmt_template(step.get("role_id", ""), context)
            if not user_raw.isdigit() or not role_raw.isdigit():
                raise ValueError("Workflow role step needs user_id and role_id")
            member = guild.get_member(int(user_raw)) or await guild.fetch_member(int(user_raw))
            role = guild.get_role(int(role_raw))
            if role is None:
                raise ValueError("Workflow role not found")
            await member.add_roles(role, reason="Dashboard workflow")
            return f"role->{role.id}"
        if kind == "reminder":
            user_raw = _fmt_template(step.get("user_id", context.get("user_id", "")), context)
            if not user_raw.isdigit():
                raise ValueError("Workflow reminder needs user_id")
            channel_raw = _fmt_template(step.get("channel_id", ""), context)
            channel_id = int(channel_raw) if channel_raw.isdigit() else None
            delay = max(1, min(43200, int(step.get("delay_minutes", 60) or 60)))
            due_at = (datetime.now(UTC) + timedelta(minutes=delay)).replace(microsecond=0).isoformat()
            await self.bot.database.execute(
                "INSERT INTO reminders(guild_id,channel_id,user_id,message,due_at) VALUES(?,?,?,?,?)",
                (guild.id, channel_id, int(user_raw), _fmt_template(step.get("message", "Workflow reminder"), context)[:1000], due_at),
            )
            return f"reminder+{delay}m"
        await self.activity(guild.id, "workflow", _fmt_template(step.get("text", "Workflow step"), context)[:180], detail=json.dumps(context, ensure_ascii=False)[:1000], source="dashboard.workflow")
        return "logged"

    async def _run_scheduled_messages(self) -> None:
        rows = await self.bot.database.fetchall(
            "SELECT * FROM dashboard_scheduled_messages WHERE status='pending' AND send_at<=CURRENT_TIMESTAMP ORDER BY id LIMIT 10"
        )
        for row in rows:
            status = "done"
            result = "sent"
            try:
                guild = self.bot.get_guild(int(row["guild_id"]))
                channel = guild.get_channel(int(row["channel_id"])) if guild else None
                if not isinstance(channel, discord.abc.Messageable):
                    raise ValueError("Channel not found")
                embed_data = _json(row["embed_json"], {})
                buttons = _json(row["buttons_json"], [])
                embed = None
                if embed_data:
                    color_raw = str(embed_data.get("color", "5865F2")).replace("#", "")
                    try:
                        color = int(color_raw, 16)
                    except ValueError:
                        color = 0x5865F2
                    embed = discord.Embed(
                        title=str(embed_data.get("title", ""))[:256] or None,
                        description=str(embed_data.get("description", ""))[:4096] or None,
                        color=color,
                    )
                    if str(embed_data.get("footer", "")).strip():
                        embed.set_footer(text=str(embed_data["footer"])[:2048])
                    if str(embed_data.get("image", "")).startswith("https://"):
                        embed.set_image(url=str(embed_data["image"])[:1000])
                    if str(embed_data.get("thumbnail", "")).startswith("https://"):
                        embed.set_thumbnail(url=str(embed_data["thumbnail"])[:1000])
                view = None
                if buttons:
                    view = discord.ui.View(timeout=None)
                    for button in buttons[:5]:
                        url = str(button.get("url", "")).strip()
                        label = str(button.get("label", "Link"))[:80]
                        if url.startswith("https://"):
                            view.add_item(discord.ui.Button(label=label, url=url))
                await channel.send(
                    content=str(row["content"] or "")[:1900] or None,
                    embed=embed,
                    view=view,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                await self.activity(int(row["guild_id"]), "scheduled_message", "Geplante Nachricht gesendet", detail=f"Channel {row['channel_id']}", source="dashboard.message")
            except Exception as exc:
                status = "failed"
                result = f"{type(exc).__name__}: {exc}"[:800]
            await self.bot.database.execute(
                "UPDATE dashboard_scheduled_messages SET status=?,result=?,processed_at=CURRENT_TIMESTAMP WHERE id=?",
                (status, result, row["id"]),
            )

    @tasks.loop(seconds=30)
    async def workflow_loop(self) -> None:
        await self._run_scheduled_messages()
        rows = await self.bot.database.fetchall("SELECT * FROM dashboard_workflows WHERE enabled=1 ORDER BY id")
        for row in rows:
            workflow_id = int(row["id"])
            guild_id = int(row["guild_id"])
            guild = self.bot.get_guild(guild_id)
            if guild is None:
                continue
            try:
                trigger = _json(row["trigger_json"], {})
                steps = _json(row["steps_json"], [])
                state_row = await self.bot.database.fetchone("SELECT cursor_json FROM dashboard_workflow_state WHERE workflow_id=?", (workflow_id,))
                state = _json(state_row["cursor_json"] if state_row else "{}", {})
                contexts, new_state = await self._workflow_contexts(row, trigger, state)
                await self.bot.database.execute(
                    """INSERT INTO dashboard_workflow_state(workflow_id,cursor_json,last_run_at,updated_at)
                    VALUES(?,?,?,CURRENT_TIMESTAMP)
                    ON CONFLICT(workflow_id) DO UPDATE SET cursor_json=excluded.cursor_json,last_run_at=excluded.last_run_at,updated_at=CURRENT_TIMESTAMP""",
                    (workflow_id, json.dumps(new_state, separators=(",", ":")), new_state.get("last_run")),
                )
                for context in contexts[:20]:
                    results: list[str] = []
                    for step in steps[:12]:
                        if isinstance(step, dict):
                            results.append(await self._run_step(guild, step, context))
                    await self.bot.database.execute(
                        "INSERT INTO dashboard_workflow_runs(workflow_id,guild_id,status,detail) VALUES(?,?,'done',?)",
                        (workflow_id, guild_id, ", ".join(results)[:1000]),
                    )
                    await self.activity(guild_id, "workflow", str(row["name"]), detail=", ".join(results), source="dashboard.workflow")
            except Exception as exc:
                logger.warning("Dashboard workflow %s failed", workflow_id, exc_info=True)
                await self.bot.database.execute(
                    "INSERT INTO dashboard_workflow_runs(workflow_id,guild_id,status,detail) VALUES(?,?,'failed',?)",
                    (workflow_id, guild_id, f"{type(exc).__name__}: {exc}"[:1000]),
                )

    @workflow_loop.before_loop
    async def before_workflow_loop(self) -> None:
        await self.bot.wait_until_ready()

    @tasks.loop(hours=1)
    async def cleanup_loop(self) -> None:
        await self.bot.database.execute("DELETE FROM dashboard_activity WHERE created_at<datetime('now','-30 days')")
        await self.bot.database.execute("DELETE FROM dashboard_error_events WHERE created_at<datetime('now','-60 days')")
        await self.bot.database.execute("DELETE FROM dashboard_workflow_runs WHERE created_at<datetime('now','-30 days')")

    @cleanup_loop.before_loop
    async def before_cleanup_loop(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(DashboardTelemetry(bot))
