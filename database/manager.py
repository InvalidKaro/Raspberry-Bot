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
        await self._connection.execute("PRAGMA synchronous=NORMAL;")
        await self._connection.execute("PRAGMA temp_store=MEMORY;")
        await self._connection.execute("PRAGMA cache_size=-4096;")
        await self._connection.execute("PRAGMA wal_autocheckpoint=1000;")
        await self._connection.commit()
        await self.initialize_schema()
        logger.info("Database connected: %s", self.path)

    async def initialize_schema(self) -> None:
        schema = """
        CREATE TABLE IF NOT EXISTS guild_settings (guild_id INTEGER PRIMARY KEY,embed_color INTEGER,ticket_category_id INTEGER,ticket_log_channel_id INTEGER,welcome_channel_id INTEGER,suggestion_channel_id INTEGER,general_log_channel_id INTEGER,auto_role_id INTEGER,welcome_message TEXT,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS ticket_staff_roles (guild_id INTEGER NOT NULL, role_id INTEGER NOT NULL, permission_level INTEGER NOT NULL DEFAULT 10, PRIMARY KEY (guild_id, role_id));
        CREATE TABLE IF NOT EXISTS tickets (id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER NOT NULL,channel_id INTEGER UNIQUE,control_message_id INTEGER,opener_id INTEGER NOT NULL,category_name TEXT,subject TEXT NOT NULL,description TEXT NOT NULL,priority TEXT NOT NULL DEFAULT 'normal',status TEXT NOT NULL DEFAULT 'open',claimed_by INTEGER,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,closed_at TEXT,closed_by INTEGER,close_reason TEXT);
        CREATE INDEX IF NOT EXISTS idx_tickets_guild_status ON tickets(guild_id,status);
        CREATE INDEX IF NOT EXISTS idx_tickets_channel ON tickets(channel_id);
        CREATE TABLE IF NOT EXISTS ticket_members (ticket_id INTEGER NOT NULL,user_id INTEGER NOT NULL,added_by INTEGER,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(ticket_id,user_id),FOREIGN KEY(ticket_id) REFERENCES tickets(id) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS ticket_events (id INTEGER PRIMARY KEY AUTOINCREMENT,ticket_id INTEGER NOT NULL,guild_id INTEGER NOT NULL,actor_id INTEGER,event_type TEXT NOT NULL,old_value TEXT,new_value TEXT,metadata TEXT,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,FOREIGN KEY(ticket_id) REFERENCES tickets(id) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS ticket_notes (id INTEGER PRIMARY KEY AUTOINCREMENT,ticket_id INTEGER NOT NULL,author_id INTEGER NOT NULL,content TEXT NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,FOREIGN KEY(ticket_id) REFERENCES tickets(id) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS moderation_cases (id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER NOT NULL,user_id INTEGER NOT NULL,moderator_id INTEGER NOT NULL,action TEXT NOT NULL,reason TEXT,duration_seconds INTEGER,active INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,expires_at TEXT);
        CREATE INDEX IF NOT EXISTS idx_mod_cases_user ON moderation_cases(guild_id,user_id);
        CREATE TABLE IF NOT EXISTS suggestions (id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER NOT NULL,message_id INTEGER,channel_id INTEGER,author_id INTEGER NOT NULL,content TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'open',created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS suggestion_votes (suggestion_id INTEGER NOT NULL,user_id INTEGER NOT NULL,vote INTEGER NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(suggestion_id,user_id),FOREIGN KEY(suggestion_id) REFERENCES suggestions(id) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS polls (id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER NOT NULL,channel_id INTEGER NOT NULL,message_id INTEGER,author_id INTEGER NOT NULL,question TEXT NOT NULL,options_json TEXT NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS poll_votes (poll_id INTEGER NOT NULL,user_id INTEGER NOT NULL,option_index INTEGER NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY (poll_id,user_id),FOREIGN KEY(poll_id) REFERENCES polls(id) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS system_monitor_config (guild_id INTEGER PRIMARY KEY,enabled INTEGER NOT NULL DEFAULT 0,status_channel_id INTEGER,status_message_id INTEGER,alert_channel_id INTEGER,interval_seconds INTEGER NOT NULL DEFAULT 30,temp_warning REAL NOT NULL DEFAULT 70,temp_critical REAL NOT NULL DEFAULT 80,ram_warning REAL NOT NULL DEFAULT 80,disk_warning REAL NOT NULL DEFAULT 85,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS system_metrics (id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER NOT NULL,recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,cpu_percent REAL NOT NULL,temperature REAL,ram_percent REAL NOT NULL,disk_percent REAL NOT NULL,load_1m REAL NOT NULL,network_rx INTEGER NOT NULL,network_tx INTEGER NOT NULL,bot_memory INTEGER NOT NULL,throttled_flags INTEGER NOT NULL DEFAULT 0);
        CREATE INDEX IF NOT EXISTS idx_metrics_guild_time ON system_metrics(guild_id,recorded_at);
        CREATE TABLE IF NOT EXISTS reminders (id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER,channel_id INTEGER,user_id INTEGER NOT NULL,message TEXT NOT NULL,due_at TEXT NOT NULL,delivered INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,delivered_at TEXT);
        CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(delivered,due_at);
        CREATE INDEX IF NOT EXISTS idx_reminders_user ON reminders(user_id,delivered,due_at);
        CREATE TABLE IF NOT EXISTS command_usage (id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER,user_id INTEGER NOT NULL,command_name TEXT NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE INDEX IF NOT EXISTS idx_command_usage_time ON command_usage(created_at);
        CREATE INDEX IF NOT EXISTS idx_command_usage_guild_time ON command_usage(guild_id,created_at);

        CREATE TABLE IF NOT EXISTS personnel_datasets (id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER NOT NULL,name TEXT NOT NULL,title TEXT NOT NULL,chart_type TEXT NOT NULL DEFAULT 'bar',x_label TEXT NOT NULL DEFAULT 'Zeitraum',y_label TEXT NOT NULL DEFAULT 'Anzahl',labels_json TEXT NOT NULL,values_json TEXT NOT NULL,series_name TEXT NOT NULL DEFAULT 'Wert',second_values_json TEXT,second_series_name TEXT,created_by INTEGER NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,UNIQUE(guild_id,name));
        CREATE INDEX IF NOT EXISTS idx_personnel_datasets_guild ON personnel_datasets(guild_id,updated_at);
        CREATE TABLE IF NOT EXISTS personnel_members (id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER NOT NULL,user_id INTEGER,display_name TEXT NOT NULL,rank_name TEXT,department TEXT,active INTEGER NOT NULL DEFAULT 1,created_by INTEGER NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,UNIQUE(guild_id,display_name));
        CREATE INDEX IF NOT EXISTS idx_personnel_members_guild_active ON personnel_members(guild_id,active,display_name);
        CREATE TABLE IF NOT EXISTS personnel_records (id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER NOT NULL,personnel_id INTEGER NOT NULL,record_date TEXT NOT NULL,period_key TEXT NOT NULL,inductions INTEGER NOT NULL DEFAULT 0,bwg INTEGER NOT NULL DEFAULT 0,note TEXT,created_by INTEGER NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,FOREIGN KEY(personnel_id) REFERENCES personnel_members(id) ON DELETE CASCADE);
        CREATE INDEX IF NOT EXISTS idx_personnel_records_member_date ON personnel_records(guild_id,personnel_id,record_date);
        CREATE INDEX IF NOT EXISTS idx_personnel_records_period ON personnel_records(guild_id,period_key);
        CREATE TABLE IF NOT EXISTS personnel_notes (id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER NOT NULL,personnel_id INTEGER NOT NULL,content TEXT NOT NULL,created_by INTEGER NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,FOREIGN KEY(personnel_id) REFERENCES personnel_members(id) ON DELETE CASCADE);
        CREATE INDEX IF NOT EXISTS idx_personnel_notes_member ON personnel_notes(guild_id,personnel_id,created_at);
        CREATE TABLE IF NOT EXISTS personnel_qualifications (id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER NOT NULL,personnel_id INTEGER NOT NULL,name TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'bestanden',created_by INTEGER NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,UNIQUE(guild_id,personnel_id,name),FOREIGN KEY(personnel_id) REFERENCES personnel_members(id) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS personnel_rank_history (id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER NOT NULL,personnel_id INTEGER NOT NULL,old_rank TEXT,new_rank TEXT,changed_by INTEGER NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,FOREIGN KEY(personnel_id) REFERENCES personnel_members(id) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS personnel_goals (id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER NOT NULL,personnel_id INTEGER,target_type TEXT NOT NULL,target_value INTEGER NOT NULL,period_key TEXT NOT NULL,created_by INTEGER NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,UNIQUE(guild_id,personnel_id,target_type,period_key));

        CREATE TABLE IF NOT EXISTS ticket_feedback (ticket_id INTEGER PRIMARY KEY,guild_id INTEGER NOT NULL,user_id INTEGER NOT NULL,rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),comment TEXT,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS bot_access_roles (guild_id INTEGER NOT NULL,role_id INTEGER NOT NULL,level TEXT NOT NULL,created_by INTEGER NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(guild_id,role_id));
        CREATE TABLE IF NOT EXISTS bot_audit_log (id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER,actor_id INTEGER,action TEXT NOT NULL,target_type TEXT,target_id TEXT,before_json TEXT,after_json TEXT,metadata_json TEXT,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE INDEX IF NOT EXISTS idx_bot_audit_guild_time ON bot_audit_log(guild_id,created_at);
        CREATE TABLE IF NOT EXISTS command_analytics (id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER,user_id INTEGER NOT NULL,command_name TEXT NOT NULL,success INTEGER NOT NULL,duration_ms REAL,error_type TEXT,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE INDEX IF NOT EXISTS idx_command_analytics_time ON command_analytics(created_at);
        CREATE INDEX IF NOT EXISTS idx_command_analytics_command ON command_analytics(command_name,created_at);
        CREATE TABLE IF NOT EXISTS maintenance_state (id INTEGER PRIMARY KEY CHECK(id=1),enabled INTEGER NOT NULL DEFAULT 0,enabled_by INTEGER,reason TEXT,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        INSERT OR IGNORE INTO maintenance_state(id,enabled) VALUES(1,0);
        CREATE TABLE IF NOT EXISTS button_roles (guild_id INTEGER NOT NULL,message_id INTEGER NOT NULL,channel_id INTEGER NOT NULL,role_id INTEGER NOT NULL,label TEXT NOT NULL,emoji TEXT,created_by INTEGER NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(message_id,role_id));
        CREATE TABLE IF NOT EXISTS onboarding_rules (guild_id INTEGER PRIMARY KEY,channel_id INTEGER,role_id INTEGER,message_id INTEGER,rules_text TEXT,updated_by INTEGER,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS dashboard_commands (id INTEGER PRIMARY KEY AUTOINCREMENT,action TEXT NOT NULL,payload_json TEXT,status TEXT NOT NULL DEFAULT 'pending',result TEXT,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,processed_at TEXT);
        CREATE TABLE IF NOT EXISTS system_snapshots_v4 (id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER NOT NULL,recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,cpu_percent REAL,ram_percent REAL,temperature REAL,disk_percent REAL,pihole_ok INTEGER,tailscale_ok INTEGER,extra_json TEXT);
        CREATE INDEX IF NOT EXISTS idx_system_snapshots_v4_time ON system_snapshots_v4(guild_id,recorded_at);
        CREATE TABLE IF NOT EXISTS backup_history (id INTEGER PRIMARY KEY AUTOINCREMENT,file_name TEXT NOT NULL,kind TEXT NOT NULL,size_bytes INTEGER NOT NULL,created_by INTEGER,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);

        CREATE TABLE IF NOT EXISTS content_templates (id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER NOT NULL,name TEXT NOT NULL,title TEXT NOT NULL,body TEXT NOT NULL,color INTEGER,created_by INTEGER NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,UNIQUE(guild_id,name));
        CREATE TABLE IF NOT EXISTS forms (id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER NOT NULL,name TEXT NOT NULL,title TEXT NOT NULL,questions_json TEXT NOT NULL,created_by INTEGER NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,UNIQUE(guild_id,name));
        CREATE TABLE IF NOT EXISTS form_responses (id INTEGER PRIMARY KEY AUTOINCREMENT,form_id INTEGER NOT NULL,guild_id INTEGER NOT NULL,user_id INTEGER NOT NULL,response_json TEXT NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,FOREIGN KEY(form_id) REFERENCES forms(id) ON DELETE CASCADE);
        CREATE INDEX IF NOT EXISTS idx_form_responses_form_time ON form_responses(form_id,created_at);
        CREATE TABLE IF NOT EXISTS panel_messages (id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER NOT NULL,channel_id INTEGER,message_id INTEGER,title TEXT NOT NULL,created_by INTEGER NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS panel_actions (id INTEGER PRIMARY KEY AUTOINCREMENT,panel_id INTEGER NOT NULL,guild_id INTEGER NOT NULL,label TEXT NOT NULL,action_type TEXT NOT NULL,value TEXT NOT NULL,position INTEGER NOT NULL DEFAULT 0,FOREIGN KEY(panel_id) REFERENCES panel_messages(id) ON DELETE CASCADE);

        CREATE TABLE IF NOT EXISTS planner_entries (id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER NOT NULL,event_date TEXT NOT NULL,start_time TEXT NOT NULL,title TEXT NOT NULL,owner_text TEXT,category TEXT NOT NULL DEFAULT 'Termin',created_by INTEGER NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE INDEX IF NOT EXISTS idx_planner_guild_date ON planner_entries(guild_id,event_date,start_time);
        CREATE TABLE IF NOT EXISTS workspace_tasks (id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER NOT NULL,title TEXT NOT NULL,details TEXT,status TEXT NOT NULL DEFAULT 'open',assigned_to INTEGER,due_at TEXT,created_by INTEGER NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE INDEX IF NOT EXISTS idx_workspace_tasks_guild_status ON workspace_tasks(guild_id,status,due_at);
        CREATE TABLE IF NOT EXISTS workspace_events (id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER NOT NULL,title TEXT NOT NULL,description TEXT,starts_at TEXT NOT NULL,channel_id INTEGER,created_by INTEGER NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE INDEX IF NOT EXISTS idx_workspace_events_guild_start ON workspace_events(guild_id,starts_at);
        CREATE TABLE IF NOT EXISTS event_rsvps (event_id INTEGER NOT NULL,user_id INTEGER NOT NULL,status TEXT NOT NULL,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(event_id,user_id),FOREIGN KEY(event_id) REFERENCES workspace_events(id) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS training_library (id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER NOT NULL,title TEXT NOT NULL,category TEXT NOT NULL DEFAULT 'Allgemein',content TEXT NOT NULL,source_url TEXT,created_by INTEGER NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE INDEX IF NOT EXISTS idx_training_guild_category ON training_library(guild_id,category,title);
        CREATE TABLE IF NOT EXISTS knowledge_entries (id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER NOT NULL,kind TEXT NOT NULL,entry_key TEXT NOT NULL,title TEXT NOT NULL,content TEXT NOT NULL,tags TEXT,created_by INTEGER NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,UNIQUE(guild_id,kind,entry_key));
        CREATE INDEX IF NOT EXISTS idx_knowledge_search ON knowledge_entries(guild_id,kind,entry_key,title);
        CREATE TABLE IF NOT EXISTS quiz_questions (id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER NOT NULL,category TEXT NOT NULL DEFAULT 'Allgemein',question TEXT NOT NULL,answer TEXT NOT NULL,explanation TEXT,created_by INTEGER NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);

        CREATE TABLE IF NOT EXISTS xp_profiles (guild_id INTEGER NOT NULL,user_id INTEGER NOT NULL,xp INTEGER NOT NULL DEFAULT 0,message_count INTEGER NOT NULL DEFAULT 0,level INTEGER NOT NULL DEFAULT 0,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(guild_id,user_id));
        CREATE INDEX IF NOT EXISTS idx_xp_guild_rank ON xp_profiles(guild_id,xp DESC);
        CREATE TABLE IF NOT EXISTS achievements (id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER NOT NULL,achievement_key TEXT NOT NULL,title TEXT NOT NULL,description TEXT NOT NULL,threshold_xp INTEGER,created_by INTEGER NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,UNIQUE(guild_id,achievement_key));
        CREATE TABLE IF NOT EXISTS user_achievements (guild_id INTEGER NOT NULL,user_id INTEGER NOT NULL,achievement_id INTEGER NOT NULL,unlocked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(guild_id,user_id,achievement_id),FOREIGN KEY(achievement_id) REFERENCES achievements(id) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS quotes (id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER NOT NULL,author_text TEXT NOT NULL,content TEXT NOT NULL,source_user_id INTEGER,created_by INTEGER NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS giveaways (id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER NOT NULL,channel_id INTEGER NOT NULL,message_id INTEGER,prize TEXT NOT NULL,ends_at TEXT NOT NULL,winner_count INTEGER NOT NULL DEFAULT 1,status TEXT NOT NULL DEFAULT 'open',created_by INTEGER NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,ended_at TEXT);
        CREATE INDEX IF NOT EXISTS idx_giveaways_status_end ON giveaways(status,ends_at);
        CREATE TABLE IF NOT EXISTS giveaway_entries (giveaway_id INTEGER NOT NULL,user_id INTEGER NOT NULL,joined_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(giveaway_id,user_id),FOREIGN KEY(giveaway_id) REFERENCES giveaways(id) ON DELETE CASCADE);

        CREATE TABLE IF NOT EXISTS custom_commands (id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER NOT NULL,name TEXT NOT NULL,response TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 1,created_by INTEGER NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,UNIQUE(guild_id,name));
        CREATE TABLE IF NOT EXISTS automation_jobs (id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER NOT NULL,kind TEXT NOT NULL,payload_json TEXT NOT NULL,run_at TEXT NOT NULL,repeat_minutes INTEGER NOT NULL DEFAULT 0,enabled INTEGER NOT NULL DEFAULT 1,created_by INTEGER NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,last_run_at TEXT,last_status TEXT,last_error TEXT);
        CREATE INDEX IF NOT EXISTS idx_automation_due ON automation_jobs(enabled,run_at);
        CREATE TABLE IF NOT EXISTS webhook_endpoints (id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER NOT NULL,name TEXT NOT NULL,url TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 1,created_by INTEGER NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,UNIQUE(guild_id,name));
        CREATE TABLE IF NOT EXISTS plugin_state (extension TEXT PRIMARY KEY,enabled INTEGER NOT NULL DEFAULT 1,updated_by INTEGER,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
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
