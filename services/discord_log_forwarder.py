from __future__ import annotations

import asyncio
import logging
import queue
from dataclasses import dataclass
from typing import Iterable

import discord

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ForwardedLog:
    level: int
    text: str


@dataclass(slots=True)
class LogChannelConfig:
    guild_id: int
    channel_id: int
    level: int


class DiscordQueueHandler(logging.Handler):
    """Thread-safe logging handler that only queues formatted records.

    Network I/O is intentionally performed by DiscordLogForwarder so logging
    never blocks the bot event loop and never calls Discord from worker threads.
    """

    def __init__(self, target_queue: queue.Queue[ForwardedLog], max_queue: int = 5000) -> None:
        super().__init__(level=logging.DEBUG)
        self.target_queue = target_queue
        self.max_queue = max_queue
        self.dropped = 0

    def emit(self, record: logging.LogRecord) -> None:
        # Prevent feedback loops from the forwarding transport itself.
        if record.name.startswith("services.discord_log_forwarder"):
            return
        if record.name.startswith("discord.http"):
            return

        try:
            text = self.format(record)
            item = ForwardedLog(record.levelno, text)
            try:
                self.target_queue.put_nowait(item)
            except queue.Full:
                self.dropped += 1
        except Exception:
            self.handleError(record)


class DiscordLogForwarder:
    """Batches Python logs and mirrors them into configured Discord channels."""

    def __init__(self, bot: discord.Client, *, flush_interval: float = 2.0) -> None:
        self.bot = bot
        self.flush_interval = max(1.0, float(flush_interval))
        self.queue: queue.Queue[ForwardedLog] = queue.Queue(maxsize=5000)
        self.handler = DiscordQueueHandler(self.queue)
        self.handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        self.configs: dict[int, LogChannelConfig] = {}
        self._task: asyncio.Task[None] | None = None
        self._installed = False

    def install(self) -> None:
        if self._installed:
            return
        logging.getLogger().addHandler(self.handler)
        self._installed = True

    def uninstall(self) -> None:
        if not self._installed:
            return
        try:
            logging.getLogger().removeHandler(self.handler)
        finally:
            self._installed = False

    async def start(self) -> None:
        self.install()
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="discord-log-forwarder")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self.uninstall()

    def set_config(self, guild_id: int, channel_id: int, level: int) -> None:
        self.configs[guild_id] = LogChannelConfig(guild_id, channel_id, level)

    def remove_config(self, guild_id: int) -> None:
        self.configs.pop(guild_id, None)

    def get_config(self, guild_id: int) -> LogChannelConfig | None:
        return self.configs.get(guild_id)

    async def load_configs(self) -> None:
        rows = await self.bot.database.fetchall(
            "SELECT guild_id, channel_id, min_level FROM discord_log_channels WHERE enabled = 1"
        )
        self.configs.clear()
        for row in rows:
            self.set_config(int(row["guild_id"]), int(row["channel_id"]), int(row["min_level"]))

    @staticmethod
    def _chunks(entries: Iterable[str], *, limit: int = 1750) -> list[str]:
        chunks: list[str] = []
        current = ""

        for raw in entries:
            # A single traceback line can be huge; split safely.
            pieces = [raw[i:i + limit] for i in range(0, len(raw), limit)] or [""]
            for piece in pieces:
                candidate = piece if not current else f"{current}\n{piece}"
                if len(candidate) > limit and current:
                    chunks.append(current)
                    current = piece
                else:
                    current = candidate

        if current:
            chunks.append(current)
        return chunks

    def _drain(self, max_items: int = 800) -> list[ForwardedLog]:
        items: list[ForwardedLog] = []
        for _ in range(max_items):
            try:
                items.append(self.queue.get_nowait())
            except queue.Empty:
                break

        if self.handler.dropped:
            dropped = self.handler.dropped
            self.handler.dropped = 0
            items.insert(
                0,
                ForwardedLog(
                    logging.WARNING,
                    f"Discord log queue overflow: {dropped} log record(s) were dropped.",
                ),
            )
        return items

    async def _resolve_channel(self, channel_id: int) -> discord.abc.Messageable | None:
        channel = self.bot.get_channel(channel_id)
        if channel is not None:
            return channel
        try:
            fetched = await self.bot.fetch_channel(channel_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None
        return fetched if hasattr(fetched, "send") else None

    async def _run(self) -> None:
        await self.bot.wait_until_ready()

        while not self.bot.is_closed():
            await asyncio.sleep(self.flush_interval)
            entries = self._drain()
            if not entries or not self.configs:
                continue

            # Keep each flush bounded so the logger can never monopolize Discord.
            for config in tuple(self.configs.values()):
                selected = [entry.text for entry in entries if entry.level >= config.level]
                if not selected:
                    continue

                channel = await self._resolve_channel(config.channel_id)
                if channel is None:
                    continue

                # Usually one message. During startup/tracebacks there may be more.
                for chunk in self._chunks(selected)[:8]:
                    try:
                        await channel.send(
                            f"```text\n{chunk}\n```",
                            allowed_mentions=discord.AllowedMentions.none(),
                            silent=True,
                        )
                    except (discord.Forbidden, discord.NotFound):
                        break
                    except discord.HTTPException:
                        # Avoid logging through the normal logger here, otherwise a
                        # failed log delivery could recursively create more logs.
                        break
