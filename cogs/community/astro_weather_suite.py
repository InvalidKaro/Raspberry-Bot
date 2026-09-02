from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

try:
    from sgp4.api import Satrec, jday
except ImportError:  # pragma: no cover - runtime fallback
    Satrec = None
    jday = None

WEATHER_CODES = {
    0: "Klar",
    1: "Überwiegend klar",
    2: "Teilweise bewölkt",
    3: "Bewölkt",
    45: "Nebel",
    48: "Reifnebel",
    51: "Leichter Nieselregen",
    53: "Nieselregen",
    55: "Starker Nieselregen",
    61: "Leichter Regen",
    63: "Regen",
    65: "Starker Regen",
    71: "Leichter Schneefall",
    73: "Schneefall",
    75: "Starker Schneefall",
    80: "Regenschauer",
    81: "Starke Regenschauer",
    82: "Heftige Regenschauer",
    95: "Gewitter",
    96: "Gewitter mit Hagel",
    99: "Starkes Gewitter mit Hagel",
}

SYNODIC_MONTH = 29.53058867
MOON_EPOCH = datetime(2000, 1, 6, 18, 14, tzinfo=UTC)


@dataclass(slots=True)
class Place:
    name: str
    latitude: float
    longitude: float
    timezone: str


@dataclass(slots=True)
class PassInfo:
    start: datetime
    maximum: datetime
    end: datetime
    max_elevation: float
    visible: bool


def _embed(title: str, description: str = "", color: int = 0x1F6FEB) -> discord.Embed:
    e = discord.Embed(title=title, description=description, color=color, timestamp=datetime.now(UTC))
    e.set_footer(text="Raspberry Sky · Open-Meteo / Sunrise-Sunset / CelesTrak / WhereTheISS")
    return e


def _moon_data(moment: datetime) -> tuple[str, float, float, datetime]:
    moment = moment.astimezone(UTC)
    age = ((moment - MOON_EPOCH).total_seconds() / 86400.0) % SYNODIC_MONTH
    illumination = (1.0 - math.cos(2.0 * math.pi * age / SYNODIC_MONTH)) * 50.0
    if age < 1.84566:
        name = "Neumond"
    elif age < 5.53699:
        name = "Zunehmende Sichel"
    elif age < 9.22831:
        name = "Erstes Viertel"
    elif age < 12.91963:
        name = "Zunehmender Mond"
    elif age < 16.61096:
        name = "Vollmond"
    elif age < 20.30228:
        name = "Abnehmender Mond"
    elif age < 23.99361:
        name = "Letztes Viertel"
    elif age < 27.68493:
        name = "Abnehmende Sichel"
    else:
        name = "Neumond"
    days_to_full = (SYNODIC_MONTH / 2.0 - age) % SYNODIC_MONTH
    next_full = moment + timedelta(days=days_to_full)
    return name, age, illumination, next_full


def _gmst_radians(jd_value: float) -> float:
    t = (jd_value - 2451545.0) / 36525.0
    deg = 280.46061837 + 360.98564736629 * (jd_value - 2451545.0) + 0.000387933 * t * t - t * t * t / 38710000.0
    return math.radians(deg % 360.0)


def _observer_ecef(lat_deg: float, lon_deg: float, alt_km: float = 0.0) -> tuple[float, float, float]:
    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)
    a = 6378.137
    f = 1.0 / 298.257223563
    e2 = f * (2.0 - f)
    n = a / math.sqrt(1.0 - e2 * math.sin(lat) ** 2)
    x = (n + alt_km) * math.cos(lat) * math.cos(lon)
    y = (n + alt_km) * math.cos(lat) * math.sin(lon)
    z = (n * (1.0 - e2) + alt_km) * math.sin(lat)
    return x, y, z


def _eci_to_ecef(r: tuple[float, float, float], jd_value: float) -> tuple[float, float, float]:
    theta = _gmst_radians(jd_value)
    c, s = math.cos(theta), math.sin(theta)
    x, y, z = r
    return c * x + s * y, -s * x + c * y, z


def _elevation(r_eci: tuple[float, float, float], jd_value: float, lat_deg: float, lon_deg: float) -> float:
    sx, sy, sz = _eci_to_ecef(r_eci, jd_value)
    ox, oy, oz = _observer_ecef(lat_deg, lon_deg)
    dx, dy, dz = sx - ox, sy - oy, sz - oz
    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)
    east = -math.sin(lon) * dx + math.cos(lon) * dy
    north = -math.sin(lat) * math.cos(lon) * dx - math.sin(lat) * math.sin(lon) * dy + math.cos(lat) * dz
    up = math.cos(lat) * math.cos(lon) * dx + math.cos(lat) * math.sin(lon) * dy + math.sin(lat) * dz
    return math.degrees(math.atan2(up, math.sqrt(east * east + north * north)))


def _sun_unit_eci(jd_value: float) -> tuple[float, float, float]:
    n = jd_value - 2451545.0
    mean_long = math.radians((280.460 + 0.9856474 * n) % 360.0)
    anomaly = math.radians((357.528 + 0.9856003 * n) % 360.0)
    ecliptic_long = mean_long + math.radians(1.915) * math.sin(anomaly) + math.radians(0.020) * math.sin(2.0 * anomaly)
    obliquity = math.radians(23.439 - 0.0000004 * n)
    return (
        math.cos(ecliptic_long),
        math.cos(obliquity) * math.sin(ecliptic_long),
        math.sin(obliquity) * math.sin(ecliptic_long),
    )


def _sun_altitude(jd_value: float, lat_deg: float, lon_deg: float) -> float:
    sun_ecef = _eci_to_ecef(_sun_unit_eci(jd_value), jd_value)
    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)
    up = math.cos(lat) * math.cos(lon) * sun_ecef[0] + math.cos(lat) * math.sin(lon) * sun_ecef[1] + math.sin(lat) * sun_ecef[2]
    return math.degrees(math.asin(max(-1.0, min(1.0, up))))


def _satellite_sunlit(r_eci: tuple[float, float, float], jd_value: float) -> bool:
    sun = _sun_unit_eci(jd_value)
    projection = r_eci[0] * sun[0] + r_eci[1] * sun[1] + r_eci[2] * sun[2]
    if projection >= 0:
        return True
    radius_sq = r_eci[0] ** 2 + r_eci[1] ** 2 + r_eci[2] ** 2
    perpendicular = math.sqrt(max(0.0, radius_sq - projection * projection))
    return perpendicular > 6378.137


def _predict_passes(tle1: str, tle2: str, lat: float, lon: float, *, hours: int = 48) -> list[PassInfo]:
    if Satrec is None or jday is None:
        return []
    sat = Satrec.twoline2rv(tle1, tle2)
    start_time = datetime.now(UTC)
    threshold = 10.0
    step = timedelta(seconds=30)
    passes: list[PassInfo] = []
    active_start: datetime | None = None
    max_time: datetime | None = None
    max_elev = -90.0
    max_sunlit = False
    current = start_time
    end_time = start_time + timedelta(hours=hours)
    while current <= end_time and len(passes) < 12:
        jd, fr = jday(current.year, current.month, current.day, current.hour, current.minute, current.second + current.microsecond / 1e6)
        error, position, _ = sat.sgp4(jd, fr)
        if error:
            current += step
            continue
        jd_total = jd + fr
        elev = _elevation(position, jd_total, lat, lon)
        if elev >= threshold:
            if active_start is None:
                active_start = current
                max_time = current
                max_elev = elev
                max_sunlit = _satellite_sunlit(position, jd_total)
            elif elev > max_elev:
                max_elev = elev
                max_time = current
                max_sunlit = _satellite_sunlit(position, jd_total)
        elif active_start is not None and max_time is not None:
            jd_max, fr_max = jday(max_time.year, max_time.month, max_time.day, max_time.hour, max_time.minute, max_time.second)
            dark = _sun_altitude(jd_max + fr_max, lat, lon) <= -6.0
            passes.append(PassInfo(active_start, max_time, current, max_elev, dark and max_sunlit))
            active_start = None
            max_time = None
            max_elev = -90.0
            max_sunlit = False
        current += step
    visible = [p for p in passes if p.visible]
    return (visible[:3] if visible else passes[:3])


class AstroWeatherSuite(commands.Cog):
    weatherboard = app_commands.Group(name="weatherboard", description="Dauerhaft aktualisierte Wetterkarte")

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._tle_cache: tuple[float, str, str] | None = None
        self.board_updater.start()

    async def cog_load(self) -> None:
        await self.bot.database.execute(
            """
            CREATE TABLE IF NOT EXISTS weather_boards (
                guild_id INTEGER PRIMARY KEY,
                channel_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                location TEXT NOT NULL,
                latitude REAL NOT NULL,
                longitude REAL NOT NULL,
                timezone TEXT NOT NULL,
                updated_by INTEGER NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

    async def cog_unload(self) -> None:
        self.board_updater.cancel()

    async def _json(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        timeout = aiohttp.ClientTimeout(total=12)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, params=params, headers={"User-Agent": "Raspberry-Bot/1.0"}) as response:
                response.raise_for_status()
                data = await response.json()
                if not isinstance(data, dict):
                    raise RuntimeError("Unexpected API response")
                return data

    async def _geocode(self, query: str) -> Place:
        data = await self._json(
            "https://geocoding-api.open-meteo.com/v1/search",
            {"name": query.strip(), "count": 1, "language": "de", "format": "json"},
        )
        results = data.get("results") or []
        if not results:
            raise ValueError("Ort nicht gefunden.")
        item = results[0]
        parts = [str(item.get("name") or query), str(item.get("admin1") or ""), str(item.get("country") or "")]
        name = ", ".join(part for part in parts if part)
        return Place(name, float(item["latitude"]), float(item["longitude"]), str(item.get("timezone") or "auto"))

    async def _weather(self, place: Place) -> dict[str, Any]:
        return await self._json(
            "https://api.open-meteo.com/v1/forecast",
            {
                "latitude": place.latitude,
                "longitude": place.longitude,
                "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m",
                "daily": "sunrise,sunset,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                "timezone": "auto",
                "forecast_days": 2,
            },
        )

    async def _weather_embed(self, place: Place) -> discord.Embed:
        data = await self._weather(place)
        current = data.get("current") or {}
        daily = data.get("daily") or {}
        code = int(current.get("weather_code") or 0)
        e = _embed(f"🌦️ Wetter · {place.name}", WEATHER_CODES.get(code, f"Wettercode {code}"), 0x3498DB)
        e.add_field(name="Temperatur", value=f"**{float(current.get('temperature_2m', 0)):.1f} °C**\nGefühlt {float(current.get('apparent_temperature', 0)):.1f} °C", inline=True)
        e.add_field(name="Luft / Wind", value=f"Feuchte **{int(current.get('relative_humidity_2m', 0))}%**\nWind **{float(current.get('wind_speed_10m', 0)):.1f} km/h**", inline=True)
        highs = daily.get("temperature_2m_max") or []
        lows = daily.get("temperature_2m_min") or []
        rain = daily.get("precipitation_probability_max") or []
        if highs and lows:
            e.add_field(name="Heute", value=f"{float(lows[0]):.1f}–{float(highs[0]):.1f} °C\nRegenrisiko **{int(rain[0]) if rain else 0}%**", inline=True)
        e.add_field(name="Koordinaten", value=f"`{place.latitude:.4f}, {place.longitude:.4f}`", inline=False)
        return e

    async def _sun_times(self, place: Place) -> dict[str, Any]:
        return await self._json(
            "https://api.sunrise-sunset.org/json",
            {"lat": place.latitude, "lng": place.longitude, "formatted": 0},
        )

    async def _iss_position(self) -> dict[str, Any]:
        return await self._json("https://api.wheretheiss.at/v1/satellites/25544")

    async def _tle(self) -> tuple[str, str]:
        if self._tle_cache and time.monotonic() - self._tle_cache[0] < 6 * 3600:
            return self._tle_cache[1], self._tle_cache[2]
        timeout = aiohttp.ClientTimeout(total=12)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(
                "https://celestrak.org/NORAD/elements/gp.php",
                params={"CATNR": "25544", "FORMAT": "TLE"},
                headers={"User-Agent": "Raspberry-Bot/1.0"},
            ) as response:
                response.raise_for_status()
                text = await response.text()
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        tle1 = next((line for line in lines if line.startswith("1 ")), "")
        tle2 = next((line for line in lines if line.startswith("2 ")), "")
        if not tle1 or not tle2:
            raise RuntimeError("ISS TLE unavailable")
        self._tle_cache = (time.monotonic(), tle1, tle2)
        return tle1, tle2

    async def _passes(self, place: Place) -> list[PassInfo]:
        if Satrec is None:
            return []
        tle1, tle2 = await self._tle()
        return await asyncio.to_thread(_predict_passes, tle1, tle2, place.latitude, place.longitude)

    @weatherboard.command(name="create", description="Erstellt oder ersetzt das dauerhaft aktualisierte Wetter-Embed.")
    @app_commands.default_permissions(manage_guild=True)
    async def weatherboard_create(self, interaction: discord.Interaction, ort: str, kanal: discord.TextChannel | None = None) -> None:
        if interaction.guild_id is None:
            return
        await interaction.response.defer(ephemeral=True)
        try:
            place = await self._geocode(ort)
            embed = await self._weather_embed(place)
        except Exception as exc:
            await interaction.followup.send(f"Wetterdaten konnten nicht geladen werden: `{type(exc).__name__}`", ephemeral=True)
            return
        target = kanal or interaction.channel
        if not isinstance(target, discord.TextChannel):
            await interaction.followup.send("Bitte einen Text-Channel auswählen.", ephemeral=True)
            return
        message = await target.send(embed=embed)
        await self.bot.database.execute(
            """
            INSERT INTO weather_boards(guild_id,channel_id,message_id,location,latitude,longitude,timezone,updated_by)
            VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(guild_id) DO UPDATE SET channel_id=excluded.channel_id,message_id=excluded.message_id,location=excluded.location,latitude=excluded.latitude,longitude=excluded.longitude,timezone=excluded.timezone,updated_by=excluded.updated_by,updated_at=CURRENT_TIMESTAMP
            """,
            (interaction.guild_id, target.id, message.id, place.name, place.latitude, place.longitude, place.timezone, interaction.user.id),
        )
        await interaction.followup.send(f"✅ Weatherboard in {target.mention} erstellt.", ephemeral=True)

    @weatherboard.command(name="refresh", description="Aktualisiert das Weatherboard sofort.")
    @app_commands.default_permissions(manage_guild=True)
    async def weatherboard_refresh(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            return
        await interaction.response.defer(ephemeral=True)
        ok = await self._refresh_board(interaction.guild_id)
        await interaction.followup.send("✅ Weatherboard aktualisiert." if ok else "Weatherboard nicht gefunden/erreichbar.", ephemeral=True)

    @weatherboard.command(name="remove", description="Entfernt die Weatherboard-Konfiguration.")
    @app_commands.default_permissions(manage_guild=True)
    async def weatherboard_remove(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            return
        await self.bot.database.execute("DELETE FROM weather_boards WHERE guild_id=?", (interaction.guild_id,))
        await interaction.response.send_message("Weatherboard-Konfiguration entfernt. Die alte Discord-Nachricht bleibt bestehen.", ephemeral=True)

    async def _refresh_board(self, guild_id: int) -> bool:
        row = await self.bot.database.fetchone("SELECT * FROM weather_boards WHERE guild_id=?", (guild_id,))
        if not row:
            return False
        place = Place(str(row["location"]), float(row["latitude"]), float(row["longitude"]), str(row["timezone"]))
        try:
            embed = await self._weather_embed(place)
            channel = self.bot.get_channel(int(row["channel_id"])) or await self.bot.fetch_channel(int(row["channel_id"]))
            message = await channel.fetch_message(int(row["message_id"]))
            await message.edit(embed=embed)
            return True
        except discord.NotFound:
            await self.bot.database.execute("DELETE FROM weather_boards WHERE guild_id=?", (guild_id,))
            return False
        except Exception:
            return False

    @tasks.loop(minutes=15)
    async def board_updater(self) -> None:
        rows = await self.bot.database.fetchall("SELECT guild_id FROM weather_boards ORDER BY guild_id LIMIT 100")
        for row in rows:
            await self._refresh_board(int(row["guild_id"]))
            await asyncio.sleep(0.25)

    @board_updater.before_loop
    async def before_board_updater(self) -> None:
        await self.bot.wait_until_ready()

    @app_commands.command(name="sun", description="Zeigt Sonnenaufgang, Sonnenuntergang und Dämmerung für einen Ort.")
    async def sun(self, interaction: discord.Interaction, ort: str) -> None:
        await interaction.response.defer()
        try:
            place = await self._geocode(ort)
            data = await self._sun_times(place)
            result = data.get("results") or {}
            sunrise = datetime.fromisoformat(str(result["sunrise"]).replace("Z", "+00:00"))
            sunset = datetime.fromisoformat(str(result["sunset"]).replace("Z", "+00:00"))
            dawn = datetime.fromisoformat(str(result["civil_twilight_begin"]).replace("Z", "+00:00"))
            dusk = datetime.fromisoformat(str(result["civil_twilight_end"]).replace("Z", "+00:00"))
            day_seconds = int(result.get("day_length") or (sunset - sunrise).total_seconds())
        except Exception as exc:
            await interaction.followup.send(f"Sonnendaten nicht verfügbar: `{type(exc).__name__}`", ephemeral=True)
            return
        e = _embed(f"☀️ Sonne · {place.name}", color=0xF59E0B)
        e.add_field(name="Morgendämmerung", value=discord.utils.format_dt(dawn, "t"), inline=True)
        e.add_field(name="Sonnenaufgang", value=discord.utils.format_dt(sunrise, "t"), inline=True)
        e.add_field(name="Sonnenuntergang", value=discord.utils.format_dt(sunset, "t"), inline=True)
        e.add_field(name="Abenddämmerung", value=discord.utils.format_dt(dusk, "t"), inline=True)
        e.add_field(name="Tageslänge", value=f"**{day_seconds // 3600} h {(day_seconds % 3600) // 60} min**", inline=True)
        await interaction.followup.send(embed=e)

    @app_commands.command(name="moon", description="Zeigt Mondphase, Beleuchtung und nächsten Vollmond.")
    async def moon(self, interaction: discord.Interaction) -> None:
        name, age, illumination, next_full = _moon_data(datetime.now(UTC))
        e = _embed("🌙 Mond", f"**{name}**", 0x8B5CF6)
        e.add_field(name="Beleuchtung", value=f"**{illumination:.1f}%**", inline=True)
        e.add_field(name="Mondalter", value=f"**{age:.1f} Tage**", inline=True)
        e.add_field(name="Nächster Vollmond", value=discord.utils.format_dt(next_full, "R"), inline=False)
        await interaction.response.send_message(embed=e)

    @app_commands.command(name="iss", description="Zeigt ISS-Position und nächste voraussichtlich sichtbare Überflüge.")
    async def iss(self, interaction: discord.Interaction, ort: str) -> None:
        await interaction.response.defer()
        try:
            place, position = await asyncio.gather(self._geocode(ort), self._iss_position())
            passes = await self._passes(place)
        except Exception as exc:
            await interaction.followup.send(f"ISS-Daten konnten nicht geladen werden: `{type(exc).__name__}`", ephemeral=True)
            return
        e = _embed("🛰️ ISS", f"Beobachtungsort: **{place.name}**", 0x3498DB)
        e.add_field(name="Aktuelle Position", value=f"`{float(position.get('latitude', 0)):.2f}, {float(position.get('longitude', 0)):.2f}`", inline=True)
        e.add_field(name="Höhe", value=f"**{float(position.get('altitude', 0)):.0f} km**", inline=True)
        e.add_field(name="Geschwindigkeit", value=f"**{float(position.get('velocity', 0)):.0f} km/h**", inline=True)
        if passes:
            lines = []
            for item in passes:
                marker = "👁️" if item.visible else "☁️"
                lines.append(f"{marker} {discord.utils.format_dt(item.maximum, 'R')} · max **{item.max_elevation:.0f}°** · {discord.utils.format_dt(item.start, 't')}–{discord.utils.format_dt(item.end, 't')}")
            e.add_field(name="Nächste Überflüge", value="\n".join(lines), inline=False)
            e.add_field(name="Hinweis", value="👁️ = Dunkelheit am Standort + ISS rechnerisch sonnenbeschienen. Wolken und lokale Sichtbedingungen können die Sichtbarkeit verhindern.", inline=False)
        else:
            e.add_field(name="Überflüge", value="Passberechnung nicht verfügbar. Installiere `sgp4` für die lokale Berechnung.", inline=False)
        await interaction.followup.send(embed=e)

    @app_commands.command(name="space", description="Kompaktes Astronomie-Dashboard für einen Ort.")
    async def space(self, interaction: discord.Interaction, ort: str) -> None:
        await interaction.response.defer()
        try:
            place = await self._geocode(ort)
            position, sun_data = await asyncio.gather(self._iss_position(), self._sun_times(place))
            passes = await self._passes(place)
            result = sun_data.get("results") or {}
            sunrise = datetime.fromisoformat(str(result["sunrise"]).replace("Z", "+00:00"))
            sunset = datetime.fromisoformat(str(result["sunset"]).replace("Z", "+00:00"))
        except Exception as exc:
            await interaction.followup.send(f"Space-Dashboard nicht verfügbar: `{type(exc).__name__}`", ephemeral=True)
            return
        moon_name, _, illumination, next_full = _moon_data(datetime.now(UTC))
        e = _embed(f"🌌 Space · {place.name}", "Astronomie + ISS auf einen Blick.", 0x4C1D95)
        e.add_field(name="☀️ Sonne", value=f"Aufgang {discord.utils.format_dt(sunrise, 't')}\nUntergang {discord.utils.format_dt(sunset, 't')}", inline=True)
        e.add_field(name="🌙 Mond", value=f"{moon_name}\n**{illumination:.0f}%** beleuchtet", inline=True)
        e.add_field(name="🛰️ ISS jetzt", value=f"{float(position.get('altitude', 0)):.0f} km Höhe\n{float(position.get('velocity', 0)):.0f} km/h", inline=True)
        if passes:
            first = passes[0]
            e.add_field(name="Nächster ISS-Pass", value=f"{discord.utils.format_dt(first.maximum, 'R')} · max **{first.max_elevation:.0f}°**{' · 👁️ sichtbar' if first.visible else ''}", inline=False)
        e.add_field(name="Nächster Vollmond", value=discord.utils.format_dt(next_full, "R"), inline=False)
        await interaction.followup.send(embed=e)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AstroWeatherSuite(bot))
