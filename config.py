from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    discord_token: str = Field(default="", alias="DISCORD_TOKEN")
    owner_ids_raw: str = Field(default="", alias="OWNER_IDS")
    dev_guild_id: int | None = Field(default=None, alias="DEV_GUILD_ID")
    bot_name: str = Field(default="Raspberry-Bot", alias="BOT_NAME")
    environment: str = Field(default="production", alias="ENVIRONMENT")
    database_path: Path = Field(default=Path("data/bot.sqlite3"), alias="DATABASE_PATH")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    default_embed_color: int = Field(default=0x5865F2, alias="DEFAULT_EMBED_COLOR")
    system_monitor_interval: int = Field(default=30, alias="SYSTEM_MONITOR_INTERVAL")
    system_metrics_sample_interval: int = Field(default=15, alias="SYSTEM_METRICS_SAMPLE_INTERVAL")
    dashboard_port: int = Field(default=8080, alias="DASHBOARD_PORT")
    system_temp_warning: float = Field(default=70.0, alias="SYSTEM_TEMP_WARNING")
    system_temp_critical: float = Field(default=80.0, alias="SYSTEM_TEMP_CRITICAL")
    system_ram_warning: float = Field(default=80.0, alias="SYSTEM_RAM_WARNING")
    system_disk_warning: float = Field(default=85.0, alias="SYSTEM_DISK_WARNING")
    transcript_max_messages: int = Field(default=3000, alias="TRANSCRIPT_MAX_MESSAGES")
    image_render_concurrency: int = Field(default=1, alias="IMAGE_RENDER_CONCURRENCY")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @field_validator("dev_guild_id", mode="before")
    @classmethod
    def empty_int_to_none(cls, value: object) -> object:
        if value in ("", None):
            return None
        return value

    @property
    def owner_ids(self) -> set[int]:
        if not self.owner_ids_raw.strip():
            return set()
        values: set[int] = set()
        for raw in self.owner_ids_raw.split(","):
            raw = raw.strip()
            if raw:
                values.add(int(raw))
        return values

    def validate_runtime(self) -> None:
        if not self.discord_token.strip():
            raise RuntimeError("DISCORD_TOKEN is empty. Add it to .env before starting the bot.")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
