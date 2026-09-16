from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from statistics import fmean
from typing import Any, Iterable, Mapping, Sequence


@dataclass(frozen=True, slots=True)
class ClimateSample:
    recorded_at: datetime
    temperature_c: float | None
    humidity_percent: float | None
    battery_percent: float | None


@dataclass(frozen=True, slots=True)
class ClimateStats:
    minimum: float
    average: float
    maximum: float


class GoveeClimateHistory:
    """Persist lightweight Govee climate samples in the bot's existing SQLite DB."""

    def __init__(self, database: Any) -> None:
        self.database = database

    async def ensure_schema(self) -> None:
        await self.database.execute(
            """
            CREATE TABLE IF NOT EXISTS govee_climate_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_key TEXT NOT NULL,
                model TEXT,
                recorded_at TEXT NOT NULL,
                temperature_c REAL,
                humidity_percent REAL,
                battery_percent REAL,
                UNIQUE(device_key, recorded_at)
            )
            """
        )
        await self.database.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_govee_climate_device_time
            ON govee_climate_history(device_key, recorded_at)
            """
        )

    async def record_devices(self, devices: Iterable[object]) -> int:
        await self.ensure_schema()
        timestamp = datetime.now(UTC).replace(microsecond=0).strftime("%Y-%m-%d %H:%M:%S")
        inserted = 0

        for device in devices:
            address = str(getattr(device, "address", "") or "").strip()
            if not address:
                continue

            temperature = self._as_float(getattr(device, "temperature_c", None))
            humidity = self._as_float(getattr(device, "humidity_percent", None))
            battery = self._as_float(getattr(device, "battery_percent", None))
            if temperature is None and humidity is None:
                continue

            await self.database.execute(
                """
                INSERT OR REPLACE INTO govee_climate_history(
                    device_key,
                    model,
                    recorded_at,
                    temperature_c,
                    humidity_percent,
                    battery_percent
                ) VALUES(?,?,?,?,?,?)
                """,
                (
                    address,
                    str(getattr(device, "model", "") or "Govee"),
                    timestamp,
                    temperature,
                    humidity,
                    battery,
                ),
            )
            inserted += 1

        if inserted:
            await self.prune(days=31)
        return inserted

    async def fetch_samples(
        self,
        device_key: str,
        *,
        hours: int = 24,
    ) -> list[ClimateSample]:
        await self.ensure_schema()
        safe_hours = max(1, min(int(hours), 24 * 31))
        rows = await self.database.fetchall(
            """
            SELECT recorded_at, temperature_c, humidity_percent, battery_percent
            FROM govee_climate_history
            WHERE device_key = ?
              AND recorded_at >= datetime('now', ?)
            ORDER BY recorded_at ASC
            """,
            (device_key, f"-{safe_hours} hours"),
        )
        return [self._row_to_sample(row) for row in rows]

    async def prune(self, *, days: int = 31) -> None:
        safe_days = max(1, min(int(days), 365))
        await self.database.execute(
            """
            DELETE FROM govee_climate_history
            WHERE recorded_at < datetime('now', ?)
            """,
            (f"-{safe_days} days",),
        )

    @staticmethod
    def summarize(samples: Sequence[ClimateSample]) -> dict[str, ClimateStats]:
        result: dict[str, ClimateStats] = {}

        temperatures = [
            sample.temperature_c
            for sample in samples
            if sample.temperature_c is not None
        ]
        humidities = [
            sample.humidity_percent
            for sample in samples
            if sample.humidity_percent is not None
        ]

        if temperatures:
            result["temperature"] = ClimateStats(
                minimum=min(temperatures),
                average=fmean(temperatures),
                maximum=max(temperatures),
            )
        if humidities:
            result["humidity"] = ClimateStats(
                minimum=min(humidities),
                average=fmean(humidities),
                maximum=max(humidities),
            )

        return result

    @staticmethod
    def _row_to_sample(row: Mapping[str, Any]) -> ClimateSample:
        raw_time = str(row["recorded_at"])
        try:
            recorded_at = datetime.strptime(
                raw_time,
                "%Y-%m-%d %H:%M:%S",
            ).replace(tzinfo=UTC)
        except ValueError:
            parsed = datetime.fromisoformat(raw_time)
            recorded_at = (
                parsed.replace(tzinfo=UTC)
                if parsed.tzinfo is None
                else parsed.astimezone(UTC)
            )

        return ClimateSample(
            recorded_at=recorded_at,
            temperature_c=GoveeClimateHistory._as_float(row["temperature_c"]),
            humidity_percent=GoveeClimateHistory._as_float(row["humidity_percent"]),
            battery_percent=GoveeClimateHistory._as_float(row["battery_percent"]),
        )

    @staticmethod
    def _as_float(value: object) -> float | None:
        if value is None or isinstance(value, bool):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
