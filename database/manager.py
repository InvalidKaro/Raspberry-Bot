from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Iterable

import aiosqlite

logger = logging.getLogger(__name__)


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._connection: aiosqlite.Connection | None = None
        self._write_lock = asyncio.Lock()

    @property
    def connection(self) -> aiosqlite.Connection:
        if self._connection is None:
            raise RuntimeError("Database is not connected.")
        return self._connection

    async def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = await aiosqlite.connect(self.path)
        self._connection.row_factory = aiosqlite.Row
        await self._connection.execute("PRAGMA journal_mode=WAL;")
        await self._connection.execute("PRAGMA foreign_keys=ON;")
        await self._connection.execute("PRAGMA busy_timeout=5000;")
        # Balanced defaults for a Raspberry Pi: WAL keeps reads responsive while
        # NORMAL avoids unnecessary fsync pressure for this community-bot workload.
        await self._connection.execute("PRAGMA synchronous=NORMAL;")
        await self._connection.execute("PRAGMA temp_store=MEMORY;")
        await self._connection.execute("PRAGMA cache_size=-4096;")
        await self._connection.execute("PRAGMA wal_autocheckpoint=1000;")
        await self._connection.commit()
        await self.initialize_schema()
        logger.info("Database connected: %s", self.path)

    async def initialize_schema(self) -> None:
        schema = """
        CREATE TABLE IF NOT EXISTS guild_settings (
            guild_id INTEGER PRIMARY KEY,
            embed_color INTEGER,
            ticket_category_id INTEGER,
            ticket_log_channel_id INTEGER,
            welcome_channel_id INTEGER,
            suggestion_channel_id INTEGER,
            general_log_channel_id INTEGER,
            auto_role_id INTEGER,
            welcome_message TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS ticket_staff_roles (
            guild_id INTEGER NOT NULL,
            role_id INTEGER NOT NULL,
            permission_level INTEGER NOT NULL DEFAULT 10,
            PRIMARY KEY (guild_id, role_id)
        );

        CREATE TABLE IF NOT EXISTS tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            channel_id INTEGER UNIQUE,
            control_message_id INTEGER,
            opener_id INTEGER NOT NULL,
            category_name TEXT,
            subject TEXT NOT NULL,
            description TEXT NOT NULL,
            priority TEXT NOT NULL DEFAULT 'normal',
            status TEXT NOT NULL DEFAULT 'open',
            claimed_by INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            closed_at TEXT,
            closed_by INTEGER,
            close_reason TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_tickets_guild_status
            ON tickets(guild_id, status);
        CREATE INDEX IF NOT EXISTS idx_tickets_channel
            ON tickets(channel_id);

        CREATE TABLE IF NOT EXISTS ticket_members (
            ticket_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            added_by INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(ticket_id, user_id),
            FOREIGN KEY(ticket_id) REFERENCES tickets(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS ticket_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id INTEGER NOT NULL,
            guild_id INTEGER NOT NULL,
            actor_id INTEGER,
            event_type TEXT NOT NULL,
            old_value TEXT,
            new_value TEXT,
            metadata TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(ticket_id) REFERENCES tickets(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS ticket_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id INTEGER NOT NULL,
            author_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(ticket_id) REFERENCES tickets(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS moderation_cases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            moderator_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            reason TEXT,
            duration_seconds INTEGER,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            expires_at TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_mod_cases_user
            ON moderation_cases(guild_id, user_id);

        CREATE TABLE IF NOT EXISTS suggestions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            message_id INTEGER,
            channel_id INTEGER,
            author_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS suggestion_votes (
            suggestion_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            vote INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (suggestion_id, user_id),
            FOREIGN KEY(suggestion_id) REFERENCES suggestions(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS polls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            channel_id INTEGER NOT NULL,
            message_id INTEGER,
            author_id INTEGER NOT NULL,
            question TEXT NOT NULL,
            options_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS poll_votes (
            poll_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            option_index INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (poll_id, user_id),
            FOREIGN KEY(poll_id) REFERENCES polls(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS system_monitor_config (
            guild_id INTEGER PRIMARY KEY,
            enabled INTEGER NOT NULL DEFAULT 0,
            status_channel_id INTEGER,
            status_message_id INTEGER,
            alert_channel_id INTEGER,
            interval_seconds INTEGER NOT NULL DEFAULT 30,
            temp_warning REAL NOT NULL DEFAULT 70,
            temp_critical REAL NOT NULL DEFAULT 80,
            ram_warning REAL NOT NULL DEFAULT 80,
            disk_warning REAL NOT NULL DEFAULT 85,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS system_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            cpu_percent REAL NOT NULL,
            temperature REAL,
            ram_percent REAL NOT NULL,
            disk_percent REAL NOT NULL,
            load_1m REAL NOT NULL,
            network_rx INTEGER NOT NULL,
            network_tx INTEGER NOT NULL,
            bot_memory INTEGER NOT NULL,
            throttled_flags INTEGER NOT NULL DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_metrics_guild_time
            ON system_metrics(guild_id, recorded_at);

        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER,
            channel_id INTEGER,
            user_id INTEGER NOT NULL,
            message TEXT NOT NULL,
            due_at TEXT NOT NULL,
            delivered INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            delivered_at TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_reminders_due
            ON reminders(delivered, due_at);
        CREATE INDEX IF NOT EXISTS idx_reminders_user
            ON reminders(user_id, delivered, due_at);

        CREATE TABLE IF NOT EXISTS command_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER,
            user_id INTEGER NOT NULL,
            command_name TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_command_usage_time
            ON command_usage(created_at);
        CREATE INDEX IF NOT EXISTS idx_command_usage_guild_time
            ON command_usage(guild_id, created_at);

        CREATE TABLE IF NOT EXISTS personnel_datasets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            title TEXT NOT NULL,
            chart_type TEXT NOT NULL DEFAULT 'bar',
            x_label TEXT NOT NULL DEFAULT 'Zeitraum',
            y_label TEXT NOT NULL DEFAULT 'Anzahl',
            labels_json TEXT NOT NULL,
            values_json TEXT NOT NULL,
            series_name TEXT NOT NULL DEFAULT 'Wert',
            second_values_json TEXT,
            second_series_name TEXT,
            created_by INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(guild_id, name)
        );

        CREATE INDEX IF NOT EXISTS idx_personnel_datasets_guild
            ON personnel_datasets(guild_id, updated_at);
        """
        async with self._write_lock:
            await self.connection.executescript(schema)
            await self._ensure_column("guild_settings", "general_log_channel_id", "INTEGER")
            await self._ensure_column("guild_settings", "auto_role_id", "INTEGER")
            await self._ensure_column("guild_settings", "welcome_message", "TEXT")
            await self.connection.commit()
        logger.info("Database schema initialized.")

    async def _ensure_column(self, table: str, column: str, definition: str) -> None:
        cursor = await self.connection.execute(f"PRAGMA table_info({table})")
        rows = await cursor.fetchall()
        await cursor.close()
        existing = {str(row[1]) for row in rows}
        if column not in existing:
            await self.connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    async def execute(self, query: str, parameters: Iterable[Any] = ()) -> int:
        async with self._write_lock:
            cursor = await self.connection.execute(query, tuple(parameters))
            await self.connection.commit()
            row_id = int(cursor.lastrowid or 0)
            await cursor.close()
            return row_id

    async def executemany(self, query: str, rows: Iterable[Iterable[Any]]) -> None:
        async with self._write_lock:
            await self.connection.executemany(query, [tuple(row) for row in rows])
            await self.connection.commit()

    async def fetchone(self, query: str, parameters: Iterable[Any] = ()) -> aiosqlite.Row | None:
        cursor = await self.connection.execute(query, tuple(parameters))
        row = await cursor.fetchone()
        await cursor.close()
        return row

    async def fetchall(self, query: str, parameters: Iterable[Any] = ()) -> list[aiosqlite.Row]:
        cursor = await self.connection.execute(query, tuple(parameters))
        rows = await cursor.fetchall()
        await cursor.close()
        return list(rows)

    async def optimize(self) -> None:
        async with self._write_lock:
            await self.connection.execute("PRAGMA optimize;")
            await self.connection.commit()

    async def close(self) -> None:
        if self._connection is None:
            return
        await self._connection.commit()
        await self._connection.close()
        self._connection = None
        logger.info("Database connection closed.")
