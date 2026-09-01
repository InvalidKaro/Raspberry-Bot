from __future__ import annotations

import asyncio
import socket
import time
from pathlib import Path

import discord
import psutil
from discord import app_commands
from discord.ext import commands

from helpers.embeds import EmbedFactory
from helpers.formatting import human_bytes
from services.maintenance import collect_garbage
from services.pihole import collect_pihole_stats
from services.system_metrics import collect_system_metrics, throttling_labels


def _tree_size(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    total = 0
    try:
        for item in path.rglob("*"):
            if item.is_file():
                try:
                    total += item.stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


class Developer(commands.GroupCog, group_name="dev", group_description="Bot owner maintenance commands"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id in self.bot.settings.owner_ids:
            return True
        await interaction.response.send_message(
            embed=EmbedFactory.error(title="Owner only", description="This command is restricted to configured bot owners."),
            ephemeral=True,
        )
        return False

    @app_commands.command(name="cache-stats", description="Show application cache usage.")
    async def cache_stats(self, interaction: discord.Interaction) -> None:
        stats = await self.bot.cache.stats()
        embed = EmbedFactory.system(title="Cache Status")
        for item in stats:
            embed.add_field(name=item.name, value=f"**{item.size} / {item.max_size}**\nTTL: {item.ttl}s", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="cache-clear", description="Clear one application cache or all caches.")
    async def cache_clear(self, interaction: discord.Interaction, cache_name: str = "all") -> None:
        if cache_name == "all":
            result = await self.bot.cache.clear_all()
            count = sum(result.values())
            text = f"Cleared **{count}** entries across all caches."
        else:
            try:
                count = await self.bot.cache.clear(cache_name)
            except KeyError:
                await interaction.response.send_message(
                    embed=EmbedFactory.error(title="Unknown cache", description=f"Available: {', '.join(self.bot.cache.names)}"),
                    ephemeral=True,
                )
                return
            text = f"Cleared **{count}** entries from `{cache_name}`."
        await interaction.response.send_message(embed=EmbedFactory.success(title="Cache cleared", description=text), ephemeral=True)

    @app_commands.command(name="gc", description="Run Python garbage collection and report process memory.")
    async def gc_command(self, interaction: discord.Interaction) -> None:
        result = await asyncio.to_thread(collect_garbage)
        freed = max(result.before_mb - result.after_mb, 0)
        await interaction.response.send_message(
            embed=EmbedFactory.system(
                title="Memory Cleanup",
                description=(
                    f"Before: **{result.before_mb:.1f} MB**\n"
                    f"After: **{result.after_mb:.1f} MB**\n"
                    f"RSS difference: **{freed:.1f} MB**\n"
                    f"Collected objects: **{result.collected_objects}**"
                ),
            ),
            ephemeral=True,
        )

    @app_commands.command(name="memory", description="Show detailed bot process and host memory usage.")
    async def memory(self, interaction: discord.Interaction) -> None:
        metrics = await collect_system_metrics(self.bot)
        process = psutil.Process()
        info = process.memory_info()
        embed = EmbedFactory.system(title="Bot Memory Details")
        embed.add_field(name="Bot RSS", value=human_bytes(info.rss), inline=True)
        embed.add_field(name="Bot VMS", value=human_bytes(info.vms), inline=True)
        embed.add_field(name="Host used", value=f"{metrics.ram_percent:.1f}% • {human_bytes(metrics.ram_used)}", inline=True)
        embed.add_field(name="Host available", value=human_bytes(metrics.ram_available), inline=True)
        embed.add_field(name="Swap", value=f"{metrics.swap_percent:.1f}% • {human_bytes(metrics.swap_used)}", inline=True)
        embed.add_field(name="Threads", value=str(process.num_threads()), inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="performance", description="Show CPU, event-loop, database and process performance.")
    async def performance(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        metrics = await collect_system_metrics(self.bot)

        loop = asyncio.get_running_loop()
        loop_started = loop.time()
        await asyncio.sleep(0.05)
        event_loop_lag_ms = max((loop.time() - loop_started - 0.05) * 1000, 0.0)

        db_started = time.perf_counter()
        await self.bot.database.fetchone("SELECT 1 AS ok")
        db_latency_ms = (time.perf_counter() - db_started) * 1000

        process = psutil.Process()
        embed = EmbedFactory.system(title="Performance")
        embed.add_field(
            name="CPU",
            value=(
                f"Host **{metrics.cpu_percent:.1f}%**\n"
                f"30s **{metrics.cpu_average_30s:.1f}%** · 5m **{metrics.cpu_average_5m:.1f}%**\n"
                f"Bot **{metrics.bot_cpu_percent:.1f}%**"
            ),
            inline=True,
        )
        embed.add_field(
            name="Latency",
            value=(
                f"Discord **{max(self.bot.latency * 1000, 0):.1f} ms**\n"
                f"Event loop **{event_loop_lag_ms:.1f} ms**\n"
                f"SQLite **{db_latency_ms:.1f} ms**"
            ),
            inline=True,
        )
        embed.add_field(
            name="Process",
            value=(
                f"RAM **{human_bytes(metrics.bot_memory)}**\n"
                f"Threads **{process.num_threads()}**\n"
                f"Load 1m **{metrics.load_1m:.2f}**"
            ),
            inline=True,
        )
        embed.add_field(
            name="System",
            value=(
                f"RAM **{metrics.ram_percent:.1f}%** · Swap **{metrics.swap_percent:.1f}%**\n"
                f"Temp **{metrics.temperature:.1f} °C**" if metrics.temperature is not None else
                f"RAM **{metrics.ram_percent:.1f}%** · Swap **{metrics.swap_percent:.1f}%**\nTemp **n/a**"
            ),
            inline=False,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="storage", description="Show disk, database, logs and backup storage usage.")
    async def storage(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        db_path = Path(self.bot.settings.database_path)
        if not db_path.is_absolute():
            db_path = Path.cwd() / db_path
        root = psutil.disk_usage("/")
        paths = {
            "Database": db_path,
            "SQLite WAL": Path(str(db_path) + "-wal"),
            "SQLite SHM": Path(str(db_path) + "-shm"),
            "Logs": Path("logs"),
            "Backups": Path("data/backups"),
            "Dashboard edit backups": Path("data/dashboard-edit-backups"),
        }
        sizes = await asyncio.gather(*(asyncio.to_thread(_tree_size, path) for path in paths.values()))
        embed = EmbedFactory.system(title="Storage")
        embed.add_field(
            name="Disk /",
            value=f"**{root.percent:.1f}%** used\n{human_bytes(root.used)} / {human_bytes(root.total)}\nFree **{human_bytes(root.free)}**",
            inline=False,
        )
        embed.add_field(
            name="Project data",
            value="\n".join(f"**{name}:** {human_bytes(size)}" for name, size in zip(paths, sizes)),
            inline=False,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="database-optimize", description="Run SQLite PRAGMA optimize.")
    async def database_optimize(self, interaction: discord.Interaction) -> None:
        await self.bot.database.optimize()
        await interaction.response.send_message(
            embed=EmbedFactory.success(title="Database optimized", description="SQLite PRAGMA optimize completed."),
            ephemeral=True,
        )

    @app_commands.command(name="database-stats", description="Show SQLite database size and table row counts.")
    async def database_stats(self, interaction: discord.Interaction) -> None:
        path = Path(self.bot.settings.database_path)
        if not path.is_absolute():
            path = Path.cwd() / path
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        tables = await self.bot.database.fetchall("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
        counts: list[tuple[str, int]] = []
        for row in tables[:30]:
            name = str(row["name"])
            if not name.replace("_", "").isalnum():
                continue
            try:
                count_row = await self.bot.database.fetchone(f'SELECT COUNT(*) AS c FROM "{name}"')
                counts.append((name, int(count_row["c"]) if count_row else 0))
            except Exception:
                continue
        counts.sort(key=lambda item: item[1], reverse=True)
        embed = EmbedFactory.system(title="Database Statistics")
        embed.add_field(name="Path", value=f"`{path}`", inline=False)
        embed.add_field(name="File size", value=human_bytes(size), inline=True)
        embed.add_field(name="Tables", value=str(len(tables)), inline=True)
        if counts:
            embed.add_field(name="Largest tables", value="\n".join(f"`{name}` • **{count:,}**" for name, count in counts[:12]), inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="extensions", description="Show configured and currently loaded bot extensions.")
    async def extensions(self, interaction: discord.Interaction) -> None:
        configured = tuple(getattr(__import__("bot"), "EXTENSIONS", ()))
        loaded = set(self.bot.extensions.keys())
        lines = [f"{'✅' if extension in loaded else '❌'} `{extension}`" for extension in configured]
        extras = sorted(loaded.difference(configured))
        if extras:
            lines.append("\n**Loaded extras**")
            lines.extend(f"🟦 `{name}`" for name in extras)
        description = "\n".join(lines) or "No extensions found."
        await interaction.response.send_message(embed=EmbedFactory.system(title="Extensions", description=description[:3900]), ephemeral=True)

    @app_commands.command(name="reload", description="Reload one bot extension without restarting the whole bot.")
    async def reload_extension(self, interaction: discord.Interaction, extension: str) -> None:
        try:
            await self.bot.reload_extension(extension.strip())
        except commands.ExtensionError as exc:
            await interaction.response.send_message(embed=EmbedFactory.error(title="Reload failed", description=f"`{type(exc).__name__}`\n{str(exc)[:1500]}"), ephemeral=True)
            return
        await interaction.response.send_message(embed=EmbedFactory.success(title="Extension reloaded", description=f"`{extension}`"), ephemeral=True)

    @app_commands.command(name="load", description="Load one bot extension.")
    async def load_extension(self, interaction: discord.Interaction, extension: str) -> None:
        try:
            await self.bot.load_extension(extension.strip())
        except commands.ExtensionError as exc:
            await interaction.response.send_message(embed=EmbedFactory.error(title="Load failed", description=f"`{type(exc).__name__}`\n{str(exc)[:1500]}"), ephemeral=True)
            return
        await interaction.response.send_message(embed=EmbedFactory.success(title="Extension loaded", description=f"`{extension}`"), ephemeral=True)

    @app_commands.command(name="unload", description="Unload one non-critical bot extension.")
    async def unload_extension(self, interaction: discord.Interaction, extension: str) -> None:
        extension = extension.strip()
        protected = {"cogs.management.developer", "cogs.core.help"}
        if extension in protected:
            await interaction.response.send_message(embed=EmbedFactory.error(title="Protected extension", description="This extension cannot be unloaded from Discord."), ephemeral=True)
            return
        try:
            await self.bot.unload_extension(extension)
        except commands.ExtensionError as exc:
            await interaction.response.send_message(embed=EmbedFactory.error(title="Unload failed", description=f"`{type(exc).__name__}`\n{str(exc)[:1500]}"), ephemeral=True)
            return
        await interaction.response.send_message(embed=EmbedFactory.success(title="Extension unloaded", description=f"`{extension}`"), ephemeral=True)

    @app_commands.command(name="logs", description="Show the newest Raspberry-Bot log lines.")
    async def logs(self, interaction: discord.Interaction, lines: app_commands.Range[int, 10, 100] = 30) -> None:
        path = Path("logs/bot.log")
        try:
            content = await asyncio.to_thread(path.read_text, encoding="utf-8", errors="replace")
            text = "\n".join(content.splitlines()[-int(lines):])
        except OSError as exc:
            text = f"Could not read `{path}`: {exc}"
        if len(text) > 3800:
            text = text[-3800:]
        await interaction.response.send_message(embed=EmbedFactory.system(title="Recent Bot Logs", description=f"```text\n{text}\n```"), ephemeral=True)

    @app_commands.command(name="command-stats", description="Show the most used commands in the last 24 hours.")
    async def command_stats(self, interaction: discord.Interaction) -> None:
        rows = await self.bot.database.fetchall(
            "SELECT command_name, COUNT(*) AS uses, COUNT(DISTINCT user_id) AS users "
            "FROM command_usage WHERE created_at >= datetime('now', '-24 hours') "
            "GROUP BY command_name ORDER BY uses DESC LIMIT 15"
        )
        description = "\n".join(f"**/{row['command_name']}** • {int(row['uses']):,} uses • {int(row['users']):,} users" for row in rows) or "No command usage recorded in the last 24 hours."
        await interaction.response.send_message(embed=EmbedFactory.system(title="Command Usage • 24h", description=description), ephemeral=True)

    @app_commands.command(name="diagnostics", description="Run diagnostics and give concrete optimization recommendations.")
    async def diagnostics(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        started = asyncio.get_running_loop().time()
        checks: list[tuple[str, bool, str]] = []
        recommendations: list[str] = []

        db_started = time.perf_counter()
        try:
            row = await self.bot.database.fetchone("SELECT 1 AS ok")
            db_ms = (time.perf_counter() - db_started) * 1000
            db_ok = bool(row and row["ok"] == 1)
            checks.append(("SQLite", db_ok, f"{db_ms:.1f} ms"))
            if db_ms > 100:
                recommendations.append("SQLite is slow: run `/dev database-optimize` and check disk load.")
        except Exception as exc:
            checks.append(("SQLite", False, type(exc).__name__))
            recommendations.append("SQLite check failed: inspect bot logs before restarting services.")

        metrics = await collect_system_metrics(self.bot)
        sampler_ok = metrics.sample_age_seconds <= metrics.sample_interval_seconds * 2.5
        checks.append(("System sampler", sampler_ok, f"{metrics.sample_age_seconds:.1f}s old"))
        if not sampler_ok:
            recommendations.append("System sampler is stale: restart the bot if it remains stale.")

        discord_ms = max(self.bot.latency * 1000, 0)
        checks.append(("Discord gateway", not self.bot.is_closed() and discord_ms < 1000, f"{discord_ms:.1f} ms"))
        if discord_ms > 250:
            recommendations.append("Discord latency is elevated; check network/Tailscale load before changing bot code.")

        pihole = await collect_pihole_stats()
        checks.append(("Pi-hole FTL", pihole.active, "active" if pihole.active else "inactive"))
        pihole_api_detail = "available" if pihole.api_available else ("permission limited" if pihole.permission_limited else "limited")
        checks.append(("Pi-hole API", pihole.api_available, pihole_api_detail))
        if pihole.permission_limited:
            recommendations.append("Pi-hole access is permission-limited; fix service-user permissions instead of polling harder.")

        if metrics.ram_percent >= 90:
            recommendations.append("RAM is critical: use `/dev memory`, then clear only oversized caches with `/dev cache-clear`.")
        elif metrics.ram_percent >= 80:
            recommendations.append("RAM is high: inspect `/dev memory` before forcing garbage collection.")
        if metrics.swap_percent >= 25:
            recommendations.append("Swap usage is elevated; identify the largest process and avoid repeated forced GC loops.")
        if metrics.temperature is not None and metrics.temperature >= 75:
            recommendations.append("Pi temperature is high; improve airflow/cooling before increasing workloads.")
        if metrics.disk_percent >= 90:
            recommendations.append("Disk is almost full: inspect `/dev storage` and clean old logs/backups safely.")
        throttle = throttling_labels(metrics.throttled_flags)
        if throttle:
            recommendations.append("Pi reports throttling/undervoltage: check power supply and cooling.")

        elapsed = (asyncio.get_running_loop().time() - started) * 1000
        description = "\n".join(f"{'✅' if ok else '⚠️'} **{name}:** {detail}" for name, ok, detail in checks)
        description += f"\n\nCompleted in **{elapsed:.0f} ms**."
        embed = EmbedFactory.system(title="Owner Diagnostics", description=description)
        embed.add_field(
            name="Recommendations",
            value="\n".join(f"• {item}" for item in recommendations[:8]) if recommendations else "No immediate optimization action needed.",
            inline=False,
        )
        if throttle:
            embed.add_field(name="Pi flags", value="\n".join(f"• {item}" for item in throttle[:8]), inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="dashboard", description="Send private dashboard links to the bot owner.")
    async def dashboard(self, interaction: discord.Interaction) -> None:
        import shutil
        import subprocess

        tailscale_ip = None
        binary = shutil.which("tailscale")
        if binary:
            try:
                result = await asyncio.to_thread(subprocess.run, [binary, "ip", "-4"], capture_output=True, text=True, timeout=4, check=False)
                lines = result.stdout.strip().splitlines()
                tailscale_ip = lines[0] if result.returncode == 0 and lines else None
            except (OSError, subprocess.SubprocessError):
                pass
        port = int(self.bot.settings.dashboard_port)
        lan_url = f"http://{socket.gethostname() or 'homepi'}.local:{port}"
        ts_url = f"http://{tailscale_ip}:{port}" if tailscale_ip else None
        view = discord.ui.View(timeout=120)
        if ts_url:
            view.add_item(discord.ui.Button(label="Tailscale", emoji="🔐", url=ts_url))
        view.add_item(discord.ui.Button(label="LAN", emoji="🏠", url=lan_url))
        text = f"LAN: `{lan_url}`" + (f"\nTailscale: `{ts_url}`" if ts_url else "")
        await interaction.response.send_message(embed=EmbedFactory.system(title="Private Dashboard", description=text), view=view, ephemeral=True)

    @app_commands.command(name="sync", description="Synchronize application commands.")
    async def sync(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        if self.bot.settings.dev_guild_id:
            guild = discord.Object(id=self.bot.settings.dev_guild_id)
            synced = await self.bot.tree.sync(guild=guild)
        else:
            synced = await self.bot.tree.sync()
        await interaction.followup.send(
            embed=EmbedFactory.success(title="Commands synchronized", description=f"Synced **{len(synced)}** commands."),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Developer(bot))
