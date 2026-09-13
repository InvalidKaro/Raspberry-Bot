from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from typing import Any, Literal

import discord
from discord import app_commands
from discord.ext import commands

from helpers.embeds import EmbedFactory
from services.action_registry import ActionSpec, action_specs_for_command, permission_allowed
from services.system_diagnostics import diagnose_system
from services.system_metrics import collect_system_metrics

logger = logging.getLogger(__name__)
ExecutionMode = Literal["direct", "modal", "slash"]
_SIMPLE_TYPES = {
    discord.AppCommandOptionType.string,
    discord.AppCommandOptionType.integer,
    discord.AppCommandOptionType.number,
    discord.AppCommandOptionType.boolean,
}
_USAGE_CACHE: dict[tuple[int | None, int], tuple[float, dict[str, float]]] = {}
_CONTEXT_CACHE: tuple[float, dict[str, tuple[float, str]]] | None = None


@dataclass(frozen=True, slots=True)
class RelatedCandidate:
    command: app_commands.Command
    score: float
    mode: ExecutionMode
    reason: str

    @property
    def icon(self) -> str:
        return {"direct": "▶️", "modal": "📝", "slash": "⌨️"}[self.mode]


def _walk_commands(bot: commands.Bot) -> list[app_commands.Command | app_commands.Group]:
    result: list[app_commands.Command | app_commands.Group] = []
    for root in bot.tree.get_commands():
        result.append(root)
        if isinstance(root, app_commands.Group):
            result.extend(root.walk_commands())
    return result


def _find_command(bot: commands.Bot, name: str) -> app_commands.Command | app_commands.Group | None:
    wanted = name.strip().lstrip("/").casefold()
    return next((c for c in _walk_commands(bot) if c.qualified_name.casefold() == wanted), None)


def _required(command: app_commands.Command) -> list[Any]:
    return [p for p in command.parameters if p.required]


def _execution_mode(command: app_commands.Command | app_commands.Group) -> ExecutionMode:
    if not isinstance(command, app_commands.Command):
        return "slash"
    required = _required(command)
    if not required:
        return "direct"
    if len(required) <= 5 and all(
        getattr(p, "type", None) in _SIMPLE_TYPES and not getattr(p, "autocomplete", None)
        for p in required
    ):
        return "modal"
    return "slash"


def _button_invokable(command: app_commands.Command | app_commands.Group) -> bool:
    return _execution_mode(command) == "direct"


def _related_commands(bot: commands.Bot, command_name: str) -> list[app_commands.Command | app_commands.Group]:
    current = _find_command(bot, command_name)
    if current is None:
        root = command_name.strip().lstrip("/").split(" ", 1)[0].casefold()
        return [c for c in _walk_commands(bot) if c.qualified_name.casefold().startswith(root + " ")][:8]
    parent = getattr(current, "parent", None)
    if isinstance(parent, app_commands.Group):
        return [c for c in parent.commands if c is not current][:8]
    root = current.qualified_name.split(" ", 1)[0]
    return [c for c in _walk_commands(bot) if c is not current and c.qualified_name.split(" ", 1)[0] == root][:8]


async def _usage_scores(bot: commands.Bot, guild_id: int | None, user_id: int) -> dict[str, float]:
    key = (guild_id, user_id)
    now = time.monotonic()
    cached = _USAGE_CACHE.get(key)
    if cached and now - cached[0] < 60:
        return cached[1]
    db = getattr(bot, "database", None)
    if db is None:
        return {}
    scores: dict[str, float] = {}
    try:
        rows = await db.fetchall(
            "SELECT command_name,COUNT(*) uses FROM command_usage "
            "WHERE user_id=? AND created_at>=datetime('now','-30 days') "
            "GROUP BY command_name ORDER BY uses DESC LIMIT 80",
            (user_id,),
        )
        for row in rows:
            scores[str(row["command_name"]).casefold()] = min(35.0, 12.0 * math.log1p(int(row["uses"])))
    except Exception:
        logger.exception("Could not rank Smart Actions from command history")
    if len(_USAGE_CACHE) >= 128:
        oldest = min(_USAGE_CACHE, key=lambda k: _USAGE_CACHE[k][0])
        _USAGE_CACHE.pop(oldest, None)
    _USAGE_CACHE[key] = (now, scores)
    return scores


async def _system_context_scores(bot: commands.Bot, command_name: str) -> dict[str, tuple[float, str]]:
    global _CONTEXT_CACHE
    root = command_name.strip().lstrip("/").split(" ", 1)[0].casefold()
    if root not in {"system", "admin", "overview", "pi", "config"}:
        return {}
    now = time.monotonic()
    if _CONTEXT_CACHE is not None and now - _CONTEXT_CACHE[0] < 20:
        return _CONTEXT_CACHE[1]
    try:
        metrics = await collect_system_metrics(bot)
        bonuses = diagnose_system(metrics).command_bonuses()
    except Exception:
        logger.exception("Could not build live Smart Action context")
        bonuses = {}
    _CONTEXT_CACHE = (now, bonuses)
    return bonuses


async def _rank_related(
    bot: commands.Bot,
    interaction: discord.Interaction,
    command_name: str,
    visited: tuple[str, ...],
) -> list[RelatedCandidate]:
    current = _find_command(bot, command_name)
    usage = await _usage_scores(bot, interaction.guild_id, interaction.user.id)
    live = await _system_context_scores(bot, command_name)
    visited_cf = {v.casefold() for v in visited}
    current_cf = command_name.strip().lstrip("/").casefold()
    ranked: list[RelatedCandidate] = []

    for item in _walk_commands(bot):
        if not isinstance(item, app_commands.Command) or item.qualified_name.casefold() == current_cf:
            continue
        name_cf = item.qualified_name.casefold()
        score = usage.get(name_cf, 0.0)
        reason = "häufig genutzt"
        live_bonus = live.get(name_cf)
        if live_bonus is not None:
            score += live_bonus[0]
            reason = f"Live-Diagnose: {live_bonus[1]}"
        if current is not None and getattr(current, "parent", None) is not None and getattr(current, "parent", None) is getattr(item, "parent", None):
            score += 120
            if live_bonus is None:
                reason = "gleiche Command-Gruppe"
        elif current is not None and current.qualified_name.split(" ", 1)[0] == item.qualified_name.split(" ", 1)[0]:
            score += 70
            if live_bonus is None:
                reason = "gleicher Funktionsbereich"
        elif score < 12:
            continue
        score += {"direct": 12, "modal": 7, "slash": 0}[_execution_mode(item)]
        if name_cf in visited_cf:
            score -= 150
        ranked.append(RelatedCandidate(item, score, _execution_mode(item), reason))

    ranked.sort(key=lambda c: (-c.score, {"direct": 0, "modal": 1, "slash": 2}[c.mode], c.command.qualified_name))
    return ranked[:20]


def _missing_default_permissions(command: app_commands.Command, user: discord.abc.User) -> list[str]:
    required = command.default_permissions
    if required is None:
        return []
    if not isinstance(user, discord.Member):
        return [name for name, enabled in required if enabled]
    return [name for name, enabled in required if enabled and not getattr(user.guild_permissions, name, False)]


async def _preflight(bot: commands.Bot, interaction: discord.Interaction, command: app_commands.Command) -> tuple[bool, str | None]:
    if command.guild_only and interaction.guild is None:
        return False, "Dieser Command funktioniert nur auf einem Server."
    missing = _missing_default_permissions(command, interaction.user)
    if missing:
        return False, "Fehlende Berechtigungen: `" + ", ".join(missing[:5]) + "`."

    db = getattr(bot, "database", None)
    if db is not None:
        row = await db.fetchone("SELECT enabled,reason FROM maintenance_state WHERE id=1")
        if row and int(row["enabled"]):
            owners = getattr(getattr(bot, "settings", None), "owner_ids", set())
            bypass = interaction.user.id in owners or (
                isinstance(interaction.user, discord.Member) and interaction.user.guild_permissions.administrator
            )
            if not bypass:
                return False, str(row["reason"] or "Wartungsmodus aktiv.")

    flags = getattr(bot, "feature_flags", None)
    if flags is not None:
        decision = await flags.decision(interaction.guild_id, interaction.user.id, command.qualified_name)
        if not decision.allowed:
            return False, f"`/{command.qualified_name}` ist deaktiviert."

    try:
        allowed = await command._check_can_run(interaction)  # type: ignore[attr-defined]
    except Exception as exc:
        return False, str(exc) or "Command-Check fehlgeschlagen."
    return (True, None) if allowed else (False, "Dieser Command ist hier nicht erlaubt.")


async def _send(interaction: discord.Interaction, *, embed: discord.Embed | None = None, content: str | None = None, view: discord.ui.View | None = None) -> None:
    if interaction.response.is_done():
        await interaction.followup.send(content=content, embed=embed, view=view, ephemeral=True)
    else:
        await interaction.response.send_message(content=content, embed=embed, view=view, ephemeral=True)


async def _record(bot: commands.Bot, interaction: discord.Interaction, command: str, success: bool, duration_ms: float, error: str | None) -> None:
    db = getattr(bot, "database", None)
    if db is None:
        return
    try:
        if success:
            await db.execute(
                "INSERT INTO command_usage(guild_id,user_id,command_name) VALUES(?,?,?)",
                (interaction.guild_id, interaction.user.id, command),
            )
        await db.execute(
            "INSERT INTO command_analytics(guild_id,user_id,command_name,success,duration_ms,error_type) VALUES(?,?,?,?,?,?)",
            (interaction.guild_id, interaction.user.id, command, int(success), duration_ms, error),
        )
        _USAGE_CACHE.pop((interaction.guild_id, interaction.user.id), None)
    except Exception:
        logger.exception("Could not record Smart Action /%s", command)


async def _invoke(
    bot: commands.Bot,
    interaction: discord.Interaction,
    command: app_commands.Command,
    *,
    params: dict[str, Any] | None = None,
    visited: tuple[str, ...] = (),
) -> None:
    allowed, reason = await _preflight(bot, interaction, command)
    if not allowed:
        await _send(interaction, embed=EmbedFactory.warning(title="Aktion nicht verfügbar", description=reason or "Nicht verfügbar."))
        return

    started = time.perf_counter()
    success = False
    error_type: str | None = None
    try:
        callback = command.callback
        binding = getattr(command, "binding", None)
        if binding is not None:
            await callback(binding, interaction, **(params or {}))
        else:
            await callback(interaction, **(params or {}))
        success = True
    except Exception as exc:
        error_type = type(exc).__name__
        logger.exception("Smart Action /%s failed", command.qualified_name)
        await _send(
            interaction,
            embed=EmbedFactory.error(
                title="Command fehlgeschlagen",
                description=f"`/{command.qualified_name}` konnte nicht ausgeführt werden.",
            ),
        )
    finally:
        await _record(
            bot,
            interaction,
            command.qualified_name,
            success,
            (time.perf_counter() - started) * 1000,
            error_type,
        )

    if success:
        next_view = CommandActionsView(bot, interaction.user.id, command.qualified_name, visited=(*visited, command.qualified_name))
        try:
            message = await interaction.original_response()
            if not message.components and (message.content or message.embeds or message.attachments):
                await interaction.edit_original_response(view=next_view)
            else:
                await interaction.followup.send("**Nächste sinnvolle Aktionen**", view=next_view, ephemeral=True)
        except (discord.HTTPException, discord.NotFound, discord.ClientException):
            logger.debug("Could not attach next Smart Actions after /%s", command.qualified_name)


def _parse_value(parameter: Any, raw: str) -> Any:
    text = raw.strip()
    choices = list(getattr(parameter, "choices", None) or [])
    if choices:
        for choice in choices:
            if text.casefold() in {str(choice.name).casefold(), str(choice.value).casefold()}:
                return choice.value
        raise ValueError(f"`{parameter.name}`: ungültige Auswahl.")

    option_type = getattr(parameter, "type", None)
    if option_type is discord.AppCommandOptionType.string:
        return text
    if option_type is discord.AppCommandOptionType.integer:
        value: int | float = int(text)
    elif option_type is discord.AppCommandOptionType.number:
        value = float(text.replace(",", "."))
    elif option_type is discord.AppCommandOptionType.boolean:
        if text.casefold() in {"ja", "yes", "true", "1", "an", "on"}:
            return True
        if text.casefold() in {"nein", "no", "false", "0", "aus", "off"}:
            return False
        raise ValueError(f"`{parameter.name}`: nutze Ja/Nein.")
    else:
        raise ValueError(f"`{parameter.name}` kann nicht über das Formular aufgelöst werden.")

    minimum, maximum = getattr(parameter, "min_value", None), getattr(parameter, "max_value", None)
    if minimum is not None and value < minimum:
        raise ValueError(f"`{parameter.name}` muss mindestens {minimum} sein.")
    if maximum is not None and value > maximum:
        raise ValueError(f"`{parameter.name}` darf höchstens {maximum} sein.")
    return value


class RelatedCommandModal(discord.ui.Modal):
    def __init__(self, bot: commands.Bot, command: app_commands.Command, visited: tuple[str, ...]) -> None:
        super().__init__(title=f"/{command.qualified_name}"[:45], timeout=300)
        self.bot, self.command, self.visited = bot, command, visited
        self.inputs: list[tuple[Any, discord.ui.TextInput]] = []
        for parameter in _required(command):
            choices = list(getattr(parameter, "choices", None) or [])
            placeholder = " | ".join(str(c.name) for c in choices[:5]) or str(getattr(parameter, "description", ""))
            field = discord.ui.TextInput(
                label=parameter.name.replace("_", " ").title()[:45],
                placeholder=placeholder[:100] or "Wert eingeben",
                max_length=min(4000, int(getattr(parameter, "max_length", None) or 4000)),
            )
            self.inputs.append((parameter, field))
            self.add_item(field)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            params = {parameter.name: _parse_value(parameter, str(field.value)) for parameter, field in self.inputs}
        except (ValueError, TypeError) as exc:
            await interaction.response.send_message(
                embed=EmbedFactory.warning(title="Eingabe prüfen", description=str(exc)),
                ephemeral=True,
            )
            return
        await _invoke(self.bot, interaction, self.command, params=params, visited=self.visited)


class RelatedQuickButton(discord.ui.Button):
    def __init__(self, candidate: RelatedCandidate, parent: "RelatedCommandsView") -> None:
        super().__init__(
            label=f"/{candidate.command.qualified_name}"[:80],
            emoji=candidate.icon,
            style=discord.ButtonStyle.success if candidate.mode == "direct" else discord.ButtonStyle.primary,
            row=0,
        )
        self.candidate, self.parent_view = candidate, parent

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.parent_view.execute(interaction, self.candidate)


class RelatedCommandSelect(discord.ui.Select):
    def __init__(self, candidates: list[RelatedCandidate], parent: "RelatedCommandsView") -> None:
        self.parent_view = parent
        super().__init__(
            placeholder="Weitere passende Commands …",
            options=[
                discord.SelectOption(
                    label=f"/{c.command.qualified_name}"[:100],
                    value=c.command.qualified_name[:100],
                    description=f"{c.reason} · {c.mode}"[:100],
                    emoji=c.icon,
                )
                for c in candidates[:20]
            ],
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        candidate = next((c for c in self.parent_view.candidates if c.command.qualified_name == self.values[0]), None)
        if candidate is None:
            await interaction.response.send_message("Action nicht mehr verfügbar.", ephemeral=True)
            return
        await self.parent_view.execute(interaction, candidate)


class RelatedCommandsView(discord.ui.View):
    def __init__(
        self,
        bot: commands.Bot,
        user_id: int,
        command_name: str,
        candidates: list[RelatedCandidate] | None = None,
        *,
        visited: tuple[str, ...] = (),
    ) -> None:
        super().__init__(timeout=300)
        self.bot, self.user_id, self.command_name, self.visited = bot, int(user_id), command_name, visited
        if candidates is None:
            candidates = [
                RelatedCandidate(c, 0.0, _execution_mode(c), "gleiche Command-Gruppe")
                for c in _related_commands(bot, command_name)
                if isinstance(c, app_commands.Command)
            ]
        self.candidates = candidates
        for candidate in [c for c in candidates if c.mode in {"direct", "modal"}][:4]:
            self.add_item(RelatedQuickButton(candidate, self))
        if candidates:
            self.add_item(RelatedCommandSelect(candidates, self))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message("Diese Smart-Action-Session gehört einem anderen Benutzer.", ephemeral=True)
        return False

    async def execute(self, interaction: discord.Interaction, candidate: RelatedCandidate) -> None:
        if candidate.mode == "direct":
            await _invoke(self.bot, interaction, candidate.command, visited=self.visited)
        elif candidate.mode == "modal":
            await interaction.response.send_modal(RelatedCommandModal(self.bot, candidate.command, self.visited))
        else:
            needed = ", ".join(f"`{p.name}`" for p in _required(candidate.command))
            await interaction.response.send_message(
                embed=EmbedFactory.info(
                    title=f"/{candidate.command.qualified_name}",
                    description=f"Komplexe Discord-Eingabe nötig: {needed or 'siehe Slash-Command'}",
                ),
                ephemeral=True,
            )


def related_embed(bot: commands.Bot, command_name: str) -> discord.Embed:
    candidates = [
        RelatedCandidate(c, 0.0, _execution_mode(c), "gleiche Command-Gruppe")
        for c in _related_commands(bot, command_name)
        if isinstance(c, app_commands.Command)
    ]
    return _smart_embed(command_name, candidates, ())


def _smart_embed(command_name: str, candidates: list[RelatedCandidate], visited: tuple[str, ...]) -> discord.Embed:
    lines = []
    for candidate in candidates[:10]:
        mode = {"direct": "sofort", "modal": "Eingabe", "slash": "Slash"}[candidate.mode]
        lines.append(f"{candidate.icon} `/{candidate.command.qualified_name}` · **{mode}** · {candidate.reason}")
    embed = EmbedFactory.info(
        title=f"Smart Actions · /{command_name}",
        description="\n".join(lines) or "Keine sinnvollen Folgeaktionen gefunden.",
    )
    if visited:
        embed.add_field(name="Flow", value=" → ".join(f"/{v}" for v in visited[-4:])[:1024], inline=False)
    embed.set_footer(text="▶️ direkt · 📝 Formular · ⌨️ Slash | Ranking: Live-Zustand + Kontext + Verlauf + Nutzung")
    return embed


class ActionButton(discord.ui.Button):
    def __init__(self, spec: ActionSpec, parent: "CommandActionsView") -> None:
        super().__init__(label=spec.label, emoji=spec.emoji, style=spec.style, row=0 if spec.id != "diagnostics" else 1)
        self.spec, self.parent_view = spec, parent

    async def callback(self, interaction: discord.Interaction) -> None:
        if not permission_allowed(self.spec, interaction.user):
            await interaction.response.send_message("Dafür fehlen dir Berechtigungen.", ephemeral=True)
            return
        await self.parent_view.run_action(interaction, self.spec)


class CommandActionsView(discord.ui.View):
    def __init__(
        self,
        bot: commands.Bot,
        user_id: int,
        command_name: str,
        *,
        visited: tuple[str, ...] = (),
    ) -> None:
        super().__init__(timeout=300)
        self.bot, self.user_id = bot, int(user_id)
        self.command_name = command_name.strip().lstrip("/") or "unknown"
        self.visited = tuple(dict.fromkeys((*visited, self.command_name)))[-8:]
        for spec in action_specs_for_command(self.command_name):
            self.add_item(ActionButton(spec, self))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message("Diese Actions gehören zur ursprünglichen Command-Session.", ephemeral=True)
        return False

    async def run_action(self, interaction: discord.Interaction, spec: ActionSpec) -> None:
        try:
            if spec.handler == "related":
                candidates = await _rank_related(self.bot, interaction, self.command_name, self.visited)
                view = RelatedCommandsView(self.bot, interaction.user.id, self.command_name, candidates, visited=self.visited)
                await interaction.response.send_message(
                    embed=_smart_embed(self.command_name, candidates, self.visited),
                    view=view if view.children else None,
                    ephemeral=True,
                )
                return
            if spec.handler == "services":
                from views.service_control import open_service_control
                await open_service_control(interaction)
                return

            from views.control_center import ControlCenterView, control_center_embed, run_diagnostics, run_quick_check, show_system_status
            if spec.handler == "control_center":
                await interaction.response.send_message(embed=control_center_embed(), view=ControlCenterView(self.bot, interaction.user.id), ephemeral=True)
            elif spec.handler == "quick_check":
                await run_quick_check(interaction, self.bot, interaction.user.id)
            elif spec.handler == "system_status":
                await show_system_status(interaction, self.bot, interaction.user.id)
            elif spec.handler == "diagnostics":
                await run_diagnostics(interaction, self.bot, interaction.user.id)
            else:
                await interaction.response.send_message("Diese Aktion ist derzeit nicht verfügbar.", ephemeral=True)
        except Exception:
            logger.exception("Action %s failed for /%s", spec.id, self.command_name)
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    embed=EmbedFactory.error(title="Aktion fehlgeschlagen", description="Technische Details wurden geloggt."),
                    ephemeral=True,
                )


class ErrorActionsView(CommandActionsView):
    def __init__(self, bot: commands.Bot, user_id: int, command_name: str, *, visited: tuple[str, ...] = ()) -> None:
        super().__init__(bot, user_id, command_name, visited=visited)
        if not any(isinstance(item, ActionButton) and item.spec.id == "diagnostics" for item in self.children):
            from services.action_registry import ACTION_REGISTRY
            self.add_item(ActionButton(ACTION_REGISTRY["diagnostics"], self))
