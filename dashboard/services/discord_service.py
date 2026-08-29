from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

import aiohttp


class DiscordServiceError(RuntimeError):
    pass


def read_env_value(path: Path, key: str) -> str:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise DiscordServiceError(f"Could not read bot environment file: {path}") from exc
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, value = stripped.split("=", 1)
        if name.strip().upper() == key.upper():
            value = value.strip().strip('"').strip("'")
            return value
    raise DiscordServiceError(f"{key} was not found in {path}.")


class DiscordService:
    def __init__(self, env_path: Path) -> None:
        self.env_path = env_path
        self.base = "https://discord.com/api/v10"

    def _token(self) -> str:
        token = read_env_value(self.env_path, "DISCORD_TOKEN")
        if not token:
            raise DiscordServiceError("DISCORD_TOKEN is empty.")
        return token

    async def _get(self, route: str):
        headers = {"Authorization": f"Bot {self._token()}", "User-Agent": "HomePiDashboard/2.0"}
        timeout = aiohttp.ClientTimeout(total=12)
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.get(self.base + route) as response:
                if response.status >= 400:
                    text = await response.text()
                    raise DiscordServiceError(f"Discord API returned HTTP {response.status}: {text[:200]}")
                return await response.json()

    async def guild(self, guild_id: int) -> dict:
        row = await self._get(f"/guilds/{guild_id}")
        return {"id": str(row["id"]), "name": str(row.get("name") or row["id"]), "icon": row.get("icon")}

    async def guilds(self, guild_ids: list[int]) -> list[dict]:
        result: list[dict] = []
        for guild_id in guild_ids[:100]:
            try:
                result.append(await self.guild(guild_id))
            except DiscordServiceError:
                continue
        return sorted(result, key=lambda item: item["name"].lower())

    async def channels(self, guild_id: int) -> list[dict]:
        rows = await self._get(f"/guilds/{guild_id}/channels")
        return [
            {"id": str(row["id"]), "name": str(row.get("name") or row["id"]), "type": int(row.get("type", -1))}
            for row in sorted(rows, key=lambda x: (int(x.get("position", 0)), str(x.get("name", ""))))
        ]

    async def roles(self, guild_id: int) -> list[dict]:
        rows = await self._get(f"/guilds/{guild_id}/roles")
        result = []
        for row in sorted(rows, key=lambda x: int(x.get("position", 0)), reverse=True):
            if str(row.get("name")) == "@everyone":
                continue
            result.append({"id": str(row["id"]), "name": str(row.get("name") or row["id"]), "position": int(row.get("position", 0))})
        return result
