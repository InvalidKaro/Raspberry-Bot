from __future__ import annotations

import asyncio
import json

from discord.ext import commands

from services.pihole import collect_pihole_stats


class SystemHistory(commands.Cog):
    """Persist lightweight system history without hammering a small Raspberry Pi."""

    SAMPLE_INTERVAL_SECONDS = 90
    RETENTION_DAYS = 8
    CLEANUP_EVERY_LOOPS = 40  # ~1 hour at 90s sampling

    def __init__(self, bot):
        self.bot = bot
        self.task = None
        self._loops = 0

    async def cog_load(self):
        self.task = asyncio.create_task(self.loop(), name="system-history-v4")

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

                # Retention cleanup is intentionally not executed every sample.
                self._loops += 1
                if self._loops >= self.CLEANUP_EVERY_LOOPS:
                    self._loops = 0
                    await self.bot.database.execute(
                        "DELETE FROM system_snapshots_v4 WHERE recorded_at < datetime('now', ?)",
                        (f"-{self.RETENTION_DAYS} days",),
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                # History is best-effort and must never take the bot down.
                pass

            await asyncio.sleep(self.SAMPLE_INTERVAL_SECONDS)


async def setup(bot):
    await bot.add_cog(SystemHistory(bot))
