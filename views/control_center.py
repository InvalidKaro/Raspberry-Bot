from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass
from typing import Awaitable, Callable

import discord
from discord.ext import commands

from helpers.embeds import EmbedFactory
from services.system_metrics import collect_system_metrics, throttling_labels


@dataclass(slots=True)
class CheckResult:
    name: str
    ok: bool
    detail: str


CheckFactory = Callable[[], Awaitable[CheckResult]]


def _progress_bar(done: int, total: int, width: int = 10) -> str:
    if total <= 0:
        return "░" * width
    filled = round(width * max(0, min(done, total)) / total)
    return "█" * filled + "░" * (width - filled)


def _status_icon(result: CheckResult | None, *, running: bool = False) -> str:
    if running:
        return "🔄"
    if result is None:
        return "▫️"
    return "✅" if result.ok else "⚠️"


async def _systemctl_state(name: str) -> str:
    try:
        proc = await asyncio.create_subprocess_exec(
            "systemctl",
            "is-active",
            name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=3)
        return stdout.decode("utf-8", errors="replace").strip() or "unknown"
    except (OSError, asyncio.TimeoutError):
        return "unknown"


async def _tailscale_state() -> str:
    if shutil.which("tailscale") is None:
        return "not installed"
    try:
        proc = await asyncio.create_subprocess_exec(
            "tailscale",
            "status",
            "--json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=4)
        if proc.returncode == 0 and stdout:
            return "online"
        return "offline"
    except (OSError, asyncio.TimeoutError):
        return "unknown"


def control_center_embed() -> discord.Embed:
    embed = EmbedFactory.system(
        title="Control Center",
        description=(
            "Zentrale Bedienoberfläche für Raspberry-Bot und HomePi. "
            "Wähle unten einen Bereich oder starte direkt einen Schnellcheck."
        ),
    )
    embed.add_field(
        name="Quick Actions",
        value="`Schnellcheck` · `Systemstatus` · `Diagnose`",
        inline=False,
    )
    embed.add_field(
        name="Bereiche",
        value="System · Administration · Medien · Community · Workspace",
        inline=False,
    )
    embed.add_field(
        name="UX",
        value="Aktionen bleiben in derselben Nachricht und schlagen passende nächste Schritte vor.",
        inline=False,
    )
    return embed


def _category_embed(category: str) -> discord.Embed:
    data = {
        "system": (
            "🖥️ System",
            "`/admin healthcheck` · `/admin diagnose` · `/admin anomaly` · `/overview pulse`\n"
            "Nutze **Schnellcheck** für eine geführte Live-Prüfung mit Fortschrittsanzeige.",
        ),
        "admin": (
            "🛡️ Administration",
            "`/admin permissionmap` · `/admin roleaudit` · `/config restorepoint` · `/config configdiff`",
        ),
        "media": (
            "🎧 Medien",
            "`/media radio` · `/media nowplaying` · `/media spotify` · `/media youtube` · `/media radiopanel`",
        ),
        "community": (
            "👥 Community",
            "`/social poll` · `/social suggest` · `/play arcade` · `/wizard setupwizard`",
        ),
        "workspace": (
            "🧠 Workspace",
            "`/overview handover` · `/overview timeline` · `/macro` · `/mdplan` · Workspace-Tools",
        ),
    }
    title, text = data.get(category, ("⌘ Control Center", "Bereich nicht gefunden."))
    embed = EmbedFactory.info(title=title, description=text)
    embed.add_field(
        name="Nächster Schritt",
        value="Nutze die Buttons darunter für direkte Aktionen oder springe über **Home** zurück.",
        inline=False,
    )
    return embed


class CategorySelect(discord.ui.Select):
    def __init__(self, parent: "ControlCenterView") -> None:
        self.parent_view = parent
        options = [
            discord.SelectOption(label="System", value="system", emoji="🖥️", description="Health, Diagnose und Raspberry-Pi-Status"),
            discord.SelectOption(label="Administration", value="admin", emoji="🛡️", description="Rollen, Rechte und Restorepoints"),
            discord.SelectOption(label="Medien", value="media", emoji="🎧", description="Radio, Spotify, YouTube und Now Playing"),
            discord.SelectOption(label="Community", value="community", emoji="👥", description="Polls, Games, Suggestions und Wizards"),
            discord.SelectOption(label="Workspace", value="workspace", emoji="🧠", description="Planung, Handover und Arbeitsabläufe"),
        ]
        super().__init__(placeholder="Bereich auswählen…", min_values=1, max_values=1, options=options, row=0)

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(embed=_category_embed(self.values[0]), view=self.parent_view)


class ActionSuggestionsView(discord.ui.View):
    def __init__(self, bot: commands.Bot, user_id: int, *, mode: str = "health") -> None:
        super().__init__(timeout=300)
        self.bot = bot
        self.user_id = user_id
        self.mode = mode

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message("Dieses Control-Center gehört zu einer anderen Session.", ephemeral=True)
        return False

    @discord.ui.button(label="Schnellcheck", emoji="🩺", style=discord.ButtonStyle.success, row=0)
    async def quick_check(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await run_quick_check(interaction, self.bot, self.user_id)

    @discord.ui.button(label="Systemstatus", emoji="🖥️", style=discord.ButtonStyle.primary, row=0)
    async def system_status(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await show_system_status(interaction, self.bot, self.user_id)

    @discord.ui.button(label="Diagnose", emoji="🔬", style=discord.ButtonStyle.secondary, row=0)
    async def diagnose(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await run_diagnostics(interaction, self.bot, self.user_id)

    @discord.ui.button(label="Home", emoji="⌂", style=discord.ButtonStyle.secondary, row=1)
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(
            embed=control_center_embed(),
            view=ControlCenterView(self.bot, self.user_id),
        )


class ControlCenterView(ActionSuggestionsView):
    def __init__(self, bot: commands.Bot, user_id: int) -> None:
        super().__init__(bot, user_id, mode="home")
        self.add_item(CategorySelect(self))


async def show_system_status(interaction: discord.Interaction, bot: commands.Bot, user_id: int) -> None:
    if not interaction.response.is_done():
        await interaction.response.defer()

    try:
        metrics = await collect_system_metrics(bot)
        temp = f"{metrics.temperature:.1f} °C" if metrics.temperature is not None else "n/a"
        embed = EmbedFactory.system(
            title="Systemstatus",
            description=(
                f"**CPU:** {metrics.cpu_percent:.1f}% · 30s Ø {metrics.cpu_average_30s:.1f}%\n"
                f"**RAM:** {metrics.ram_percent:.1f}%\n"
                f"**Temperatur:** {temp}\n"
                f"**Disk:** {metrics.disk_percent:.1f}%\n"
                f"**Load:** {metrics.load_1m:.2f} / {metrics.load_5m:.2f} / {metrics.load_15m:.2f}\n"
                f"**Discord:** {max(bot.latency * 1000, 0):.0f} ms\n"
                f"**Pi-hole:** {'aktiv' if metrics.pihole_active else 'nicht erreichbar'}"
            ),
        )
        throttle = throttling_labels(metrics.throttled_flags)
        if throttle:
            embed.add_field(name="Throttling", value="\n".join(f"• {item}" for item in throttle), inline=False)
    except Exception as exc:
        embed = EmbedFactory.error(title="Systemstatus fehlgeschlagen", description=f"`{type(exc).__name__}`")

    await interaction.edit_original_response(embed=embed, view=ActionSuggestionsView(bot, user_id, mode="system"))


async def run_quick_check(interaction: discord.Interaction, bot: commands.Bot, user_id: int) -> None:
    if not interaction.response.is_done():
        await interaction.response.defer()

    results: list[CheckResult | None] = [None] * 6
    labels = ["Discord", "Systemmetriken", "Datenbank", "Bot-Service", "Dashboard", "Tailscale"]

    def render(running_index: int | None = None) -> discord.Embed:
        done = sum(result is not None for result in results)
        lines = []
        for index, label in enumerate(labels):
            result = results[index]
            icon = _status_icon(result, running=running_index == index)
            detail = f" — {result.detail}" if result is not None else ""
            lines.append(f"{icon} **{label}**{detail}")
        embed = EmbedFactory.system(
            title="Schnellcheck läuft" if done < len(results) else "Schnellcheck abgeschlossen",
            description=f"`{_progress_bar(done, len(results))}` **{done}/{len(results)}**\n\n" + "\n".join(lines),
        )
        return embed

    await interaction.edit_original_response(embed=render(0), view=None)

    async def check_discord() -> CheckResult:
        latency = max(bot.latency * 1000, 0)
        return CheckResult("Discord", latency < 750, f"{latency:.0f} ms")

    async def check_metrics() -> CheckResult:
        metrics = await collect_system_metrics(bot)
        warnings: list[str] = []
        if metrics.cpu_percent >= 90:
            warnings.append(f"CPU {metrics.cpu_percent:.0f}%")
        if metrics.ram_percent >= 90:
            warnings.append(f"RAM {metrics.ram_percent:.0f}%")
        if metrics.disk_percent >= 90:
            warnings.append(f"Disk {metrics.disk_percent:.0f}%")
        if metrics.temperature is not None and metrics.temperature >= 75:
            warnings.append(f"{metrics.temperature:.1f}°C")
        detail = ", ".join(warnings) if warnings else f"CPU {metrics.cpu_percent:.0f}% · RAM {metrics.ram_percent:.0f}%"
        return CheckResult("Systemmetriken", not warnings, detail)

    async def check_db() -> CheckResult:
        try:
            row = await bot.database.fetchone("SELECT 1 AS ok")
            return CheckResult("Datenbank", bool(row and int(row["ok"]) == 1), "SQLite antwortet")
        except Exception as exc:
            return CheckResult("Datenbank", False, type(exc).__name__)

    async def check_service(name: str, label: str) -> CheckResult:
        state = await _systemctl_state(name)
        return CheckResult(label, state == "active", state)

    async def check_tailscale() -> CheckResult:
        state = await _tailscale_state()
        return CheckResult("Tailscale", state == "online", state)

    checks: list[CheckFactory] = [
        check_discord,
        check_metrics,
        check_db,
        lambda: check_service("raspberry-bot", "Bot-Service"),
        lambda: check_service("raspberry-dashboard", "Dashboard"),
        check_tailscale,
    ]

    for index, check in enumerate(checks):
        await interaction.edit_original_response(embed=render(index), view=None)
        try:
            results[index] = await check()
        except Exception as exc:
            results[index] = CheckResult(labels[index], False, type(exc).__name__)

    failed = [result for result in results if result is not None and not result.ok]
    final = render(None)
    final.title = "✅ Schnellcheck abgeschlossen" if not failed else "⚠️ Schnellcheck abgeschlossen"
    if failed:
        final.add_field(
            name="Auffällig",
            value="\n".join(f"• **{result.name}:** {result.detail}" for result in failed),
            inline=False,
        )
    final.add_field(
        name="Empfohlene Aktion",
        value="**Diagnose** öffnen, wenn du die auffälligen Punkte genauer prüfen willst." if failed else "Keine Aktion erforderlich.",
        inline=False,
    )
    await interaction.edit_original_response(embed=final, view=ActionSuggestionsView(bot, user_id, mode="health"))


async def run_diagnostics(interaction: discord.Interaction, bot: commands.Bot, user_id: int) -> None:
    if not interaction.response.is_done():
        await interaction.response.defer()

    stages = ["System", "Datenbank", "Services", "Command-Fehler", "Auswertung"]
    states = ["pending"] * len(stages)

    def render(current: int | None = None) -> discord.Embed:
        done = sum(state == "done" for state in states)
        lines = []
        for index, label in enumerate(stages):
            if current == index:
                icon = "🔄"
            elif states[index] == "done":
                icon = "✅"
            elif states[index] == "warn":
                icon = "⚠️"
            else:
                icon = "▫️"
            lines.append(f"{icon} {label}")
        return EmbedFactory.system(
            title="Diagnose läuft",
            description=f"`{_progress_bar(done, len(stages))}` **{done}/{len(stages)}**\n\n" + "\n".join(lines),
        )

    await interaction.edit_original_response(embed=render(0), view=None)
    findings: list[str] = []

    try:
        metrics = await collect_system_metrics(bot)
        if metrics.disk_percent >= 90:
            findings.append(f"Datenträger fast voll: **{metrics.disk_percent:.1f}%**")
        if metrics.ram_percent >= 90:
            findings.append(f"RAM-Auslastung hoch: **{metrics.ram_percent:.1f}%**")
        if metrics.temperature is not None and metrics.temperature >= 75:
            findings.append(f"Temperatur hoch: **{metrics.temperature:.1f} °C**")
        if metrics.throttled_flags:
            findings.append("Raspberry-Pi Throttling-Flags gesetzt: " + ", ".join(throttling_labels(metrics.throttled_flags)))
        states[0] = "done"
    except Exception as exc:
        states[0] = "warn"
        findings.append(f"Systemmetriken konnten nicht gelesen werden (`{type(exc).__name__}`).")

    await interaction.edit_original_response(embed=render(1), view=None)
    try:
        await bot.database.fetchone("SELECT 1 AS ok")
        states[1] = "done"
    except Exception as exc:
        states[1] = "warn"
        findings.append(f"SQLite reagiert nicht (`{type(exc).__name__}`).")

    await interaction.edit_original_response(embed=render(2), view=None)
    bot_state, dashboard_state = await asyncio.gather(
        _systemctl_state("raspberry-bot"),
        _systemctl_state("raspberry-dashboard"),
    )
    if bot_state != "active":
        findings.append(f"`raspberry-bot` ist **{bot_state}**.")
    if dashboard_state != "active":
        findings.append(f"`raspberry-dashboard` ist **{dashboard_state}**.")
    states[2] = "done" if bot_state == "active" and dashboard_state == "active" else "warn"

    await interaction.edit_original_response(embed=render(3), view=None)
    try:
        if interaction.guild_id is not None:
            row = await bot.database.fetchone(
                "SELECT COUNT(*) AS c FROM command_analytics WHERE guild_id=? AND success=0 AND created_at>=datetime('now','-24 hours')",
                (interaction.guild_id,),
            )
            errors = int(row["c"] or 0) if row else 0
            if errors >= 10:
                findings.append(f"**{errors}** fehlgeschlagene Commands in den letzten 24 Stunden.")
                states[3] = "warn"
            else:
                states[3] = "done"
        else:
            states[3] = "done"
    except Exception as exc:
        states[3] = "warn"
        findings.append(f"Command-Analytics nicht lesbar (`{type(exc).__name__}`).")

    await interaction.edit_original_response(embed=render(4), view=None)
    states[4] = "done"

    if findings:
        embed = EmbedFactory.warning(
            title="Diagnose abgeschlossen",
            description="\n".join(f"• {item}" for item in findings),
        )
        embed.add_field(
            name="Action Suggestions",
            value="Starte einen **Schnellcheck** zum Gegenprüfen oder öffne den **Systemstatus** für Live-Metriken.",
            inline=False,
        )
    else:
        embed = EmbedFactory.success(
            title="Diagnose abgeschlossen",
            description="Keine offensichtlichen Probleme gefunden. Kernsysteme sehen gesund aus.",
        )
        embed.add_field(
            name="Action Suggestions",
            value="Öffne den **Systemstatus** für aktuelle Live-Werte oder gehe zurück ins **Control Center**.",
            inline=False,
        )

    await interaction.edit_original_response(embed=embed, view=ActionSuggestionsView(bot, user_id, mode="diagnose"))
