from __future__ import annotations

import asyncio
import json

from discord.ext import commands

from services.pihole import collect_pihole_stats


class SystemHistory(commands.Cog):
    """Persist lightweight system history without hammering a small Raspberry Pi."""

    SAMPLE_INTERVAL_SECONDS = 90
    RAW_RETENTION_DAYS = 8
    ROLLUP_RETENTION_DAYS = 60
    CLEANUP_EVERY_LOOPS = 40  # ~1 hour at 90s sampling

    def __init__(self, bot):
        self.bot = bot
        self.task = None
        self._loops = 0

    async def cog_load(self):
        await self.bot.database.execute(
            """CREATE TABLE IF NOT EXISTS system_snapshots_hourly(
                guild_id INTEGER NOT NULL,
                hour TEXT NOT NULL,
                cpu_avg REAL,
                cpu_peak REAL,
                ram_avg REAL,
                ram_peak REAL,
                temperature_avg REAL,
                temperature_peak REAL,
                disk_avg REAL,
                load_avg REAL,
                pihole_ok INTEGER,
                tailscale_ok INTEGER,
                samples INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(guild_id,hour)
            )"""
        )
        self.task = asyncio.create_task(self.loop(), name="system-history-v5")

    async def cog_unload(self):
        if self.task:
            self.task.cancel()

    async def _tailscale_active(self) -> bool:
        try:
            proc = await asyncio.create_subprocess_exec(
                "tailscale",
                "status",
                "--json",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.communicate(), timeout=4)
            return proc.returncode == 0
        except Exception:
            return False

    async def _rollup_and_cleanup(self) -> None:
        # Keep eight days at 90-second resolution. Older data is collapsed to
        # one row per hour, giving the dashboard a 30/60-day view for only a
        # few thousand rows instead of hundreds of thousands.
        rows = await self.bot.database.fetchall(
            """SELECT guild_id,strftime('%Y-%m-%d %H:00:00',recorded_at) hour,
                AVG(cpu_percent) cpu_avg,MAX(cpu_percent) cpu_peak,
                AVG(ram_percent) ram_avg,MAX(ram_percent) ram_peak,
                AVG(temperature) temperature_avg,MAX(temperature) temperature_peak,
                AVG(disk_percent) disk_avg,
                AVG(CAST(json_extract(extra_json,'$.load') AS REAL)) load_avg,
                MIN(COALESCE(pihole_ok,0)) pihole_ok,
                MIN(COALESCE(tailscale_ok,0)) tailscale_ok,
                COUNT(*) samples
            FROM system_snapshots_v4
            WHERE recorded_at < datetime('now', ?) AND recorded_at >= datetime('now', ?)
            GROUP BY guild_id,strftime('%Y-%m-%d %H:00:00',recorded_at)""",
            (f"-{self.RAW_RETENTION_DAYS} days", f"-{self.ROLLUP_RETENTION_DAYS} days"),
        )
        for row in rows:
            await self.bot.database.execute(
                """INSERT INTO system_snapshots_hourly
                (guild_id,hour,cpu_avg,cpu_peak,ram_avg,ram_peak,temperature_avg,temperature_peak,disk_avg,load_avg,pihole_ok,tailscale_ok,samples)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(guild_id,hour) DO UPDATE SET
                    cpu_avg=excluded.cpu_avg,cpu_peak=excluded.cpu_peak,
                    ram_avg=excluded.ram_avg,ram_peak=excluded.ram_peak,
                    temperature_avg=excluded.temperature_avg,temperature_peak=excluded.temperature_peak,
                    disk_avg=excluded.disk_avg,load_avg=excluded.load_avg,
                    pihole_ok=excluded.pihole_ok,tailscale_ok=excluded.tailscale_ok,samples=excluded.samples""",
                (
                    row["guild_id"], row["hour"], row["cpu_avg"], row["cpu_peak"],
                    row["ram_avg"], row["ram_peak"], row["temperature_avg"], row["temperature_peak"],
                    row["disk_avg"], row["load_avg"], row["pihole_ok"], row["tailscale_ok"], row["samples"],
                ),
            )
        await self.bot.database.execute(
            "DELETE FROM system_snapshots_v4 WHERE recorded_at < datetime('now', ?)",
            (f"-{self.RAW_RETENTION_DAYS} days",),
        )
        await self.bot.database.execute(
            "DELETE FROM system_snapshots_hourly WHERE hour < datetime('now', ?)",
            (f"-{self.ROLLUP_RETENTION_DAYS} days",),
        )

    async def loop(self):
        await self.bot.wait_until_ready()

        while not self.bot.is_closed():
            try:
                snap = await self.bot.system_metrics.get()

                # These values are host-wide, so collect them once per cycle instead
                # of once for every Discord guild.
                pihole_task = asyncio.create_task(collect_pihole_stats())
                tailscale_task = asyncio.create_task(self._tailscale_active())
                pihole, tailscale = await asyncio.gather(pihole_task, tailscale_task)

                extra_json = json.dumps({"load": snap.load_1m}, separators=(",", ":"))
                for guild in self.bot.guilds:
                    await self.bot.database.execute(
                        """INSERT INTO system_snapshots_v4
                        (guild_id,cpu_percent,ram_percent,temperature,disk_percent,pihole_ok,tailscale_ok,extra_json)
                        VALUES(?,?,?,?,?,?,?,?)""",
                        (
                            guild.id,
                            snap.cpu_percent,
                            snap.ram_percent,
                            snap.temperature,
                            snap.disk_percent,
                            1 if pihole.active else 0,
                            1 if tailscale else 0,
                            extra_json,
                        ),
                    )

                self._loops += 1
                if self._loops >= self.CLEANUP_EVERY_LOOPS:
                    self._loops = 0
                    await self._rollup_and_cleanup()
            except asyncio.CancelledError:
                raise
            except Exception:
                # History is best-effort and must never take the bot down.
                pass

            await asyncio.sleep(self.SAMPLE_INTERVAL_SECONDS)


async def setup(bot):
    await bot.add_cog(SystemHistory(bot))
