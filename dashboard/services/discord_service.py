from __future__ import annotations

import asyncio
import time
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
        self._guild_cache_key: tuple[int, ...] = ()
        self._guild_cache_until = 0.0
        self._guild_cache: list[dict] = []

    def _token(self) -> str:
        token = read_env_value(self.env_path, "DISCORD_TOKEN")
        if not token:
            raise DiscordServiceError("DISCORD_TOKEN is empty.")
        return token

    @staticmethod
    async def _decode_response(response: aiohttp.ClientResponse):
        if response.status >= 400:
            text = await response.text()
            raise DiscordServiceError(f"Discord API returned HTTP {response.status}: {text[:300]}")
        if response.status == 204:
            return {}
        text = await response.text()
        if not text:
            return {}
        try:
            return await response.json()
        except (aiohttp.ContentTypeError, ValueError) as exc:
            raise DiscordServiceError("Discord API returned an invalid JSON response.") from exc

    async def _request(self, method: str, route: str, *, payload: dict | None = None):
        headers = {"Authorization": f"Bot {self._token()}", "User-Agent": "HomePiDashboard/4.0"}
        timeout = aiohttp.ClientTimeout(total=12)
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.request(method, self.base + route, json=payload) as response:
                return await self._decode_response(response)

    async def _get(self, route: str):
        return await self._request("GET", route)

    @staticmethod
    def _guild_payload(row: dict) -> dict:
        return {
            "id": str(row["id"]),
            "name": str(row.get("name") or row["id"]),
            "icon": row.get("icon"),
            "owner_id": str(row.get("owner_id") or ""),
            "member_count": int(row.get("approximate_member_count") or 0),
            "presence_count": int(row.get("approximate_presence_count") or 0),
            "description": row.get("description"),
            "features": list(row.get("features") or []),
        }

    async def guild(self, guild_id: int) -> dict:
        row = await self._get(f"/guilds/{guild_id}?with_counts=true")
        return self._guild_payload(row)

    async def guilds(self, guild_ids: list[int]) -> list[dict]:
        """Resolve the bot's guild list with one Discord API call when possible."""
        normalized = tuple(dict.fromkeys(int(value) for value in guild_ids if int(value) > 0))[:200]
        now = time.monotonic()
        if normalized == self._guild_cache_key and now < self._guild_cache_until:
            return [dict(item) for item in self._guild_cache]

        # Discord exposes the current bot user's guilds in one bulk request.
        # This avoids up to 100 sequential/per-guild REST calls just to populate
        # a dashboard dropdown.
        try:
            rows = await self._get("/users/@me/guilds?limit=200&with_counts=true")
            if not isinstance(rows, list):
                raise DiscordServiceError("Discord API returned an invalid guild list.")
            wanted = set(normalized)
            if wanted:
                rows = [row for row in rows if int(row.get("id", 0) or 0) in wanted]
            result = sorted((self._guild_payload(row) for row in rows), key=lambda item: item["name"].lower())
            self._guild_cache_key = normalized
            self._guild_cache_until = time.monotonic() + 30.0
            self._guild_cache = [dict(item) for item in result]
            return result
        except (DiscordServiceError, aiohttp.ClientError, asyncio.TimeoutError):
            pass

        # Compatibility fallback: still concurrent, but with one hard overall
        # deadline so a broken network can never make the dashboard hang for a minute.
        if not normalized:
            return []
        headers = {"Authorization": f"Bot {self._token()}", "User-Agent": "HomePiDashboard/4.0"}
        timeout = aiohttp.ClientTimeout(total=4, connect=2, sock_read=3)
        connector = aiohttp.TCPConnector(limit=12, ttl_dns_cache=300)
        semaphore = asyncio.Semaphore(12)

        async def run_fallback() -> list[dict]:
            async with aiohttp.ClientSession(timeout=timeout, headers=headers, connector=connector) as session:
                async def fetch_one(guild_id: int) -> dict | None:
                    try:
                        async with semaphore:
                            async with session.get(self.base + f"/guilds/{guild_id}?with_counts=true") as response:
                                row = await self._decode_response(response)
                        return self._guild_payload(row)
                    except (DiscordServiceError, aiohttp.ClientError, asyncio.TimeoutError):
                        return None
                rows = await asyncio.gather(*(fetch_one(guild_id) for guild_id in normalized))
                return [row for row in rows if row is not None]

        try:
            async with asyncio.timeout(8):
                fallback_rows = await run_fallback()
        except TimeoutError:
            fallback_rows = []

        result = sorted(fallback_rows, key=lambda item: item["name"].lower())
        self._guild_cache_key = normalized
        self._guild_cache_until = time.monotonic() + 30.0
        self._guild_cache = [dict(item) for item in result]
        return result

    async def channels_detailed(self, guild_id: int) -> list[dict]:
        rows = await self._get(f"/guilds/{guild_id}/channels")
        result: list[dict] = []
        for row in sorted(rows, key=lambda x: (int(x.get("position", 0)), str(x.get("name", "")))):
            result.append({
                "id": str(row["id"]),
                "name": str(row.get("name") or row["id"]),
                "type": int(row.get("type", -1)),
                "position": int(row.get("position", 0)),
                "parent_id": str(row.get("parent_id")) if row.get("parent_id") else None,
                "topic": row.get("topic"),
                "nsfw": bool(row.get("nsfw", False)),
                "rate_limit_per_user": int(row.get("rate_limit_per_user") or 0),
                "bitrate": int(row.get("bitrate") or 0) or None,
                "user_limit": int(row.get("user_limit", 0) or 0) or None,
                "permission_overwrites": [
                    {
                        "id": str(item.get("id") or ""),
                        "type": int(item.get("type", 0)),
                        "allow": str(item.get("allow") or "0"),
                        "deny": str(item.get("deny") or "0"),
                    }
                    for item in (row.get("permission_overwrites") or [])
                ],
            })
        return result

    async def channels(self, guild_id: int) -> list[dict]:
        rows = await self.channels_detailed(guild_id)
        return [{"id": row["id"], "name": row["name"], "type": row["type"]} for row in rows]

    async def roles_detailed(self, guild_id: int) -> list[dict]:
        rows = await self._get(f"/guilds/{guild_id}/roles")
        result = []
        for row in sorted(rows, key=lambda x: int(x.get("position", 0)), reverse=True):
            result.append({
                "id": str(row["id"]),
                "name": str(row.get("name") or row["id"]),
                "position": int(row.get("position", 0)),
                "permissions": str(row.get("permissions") or "0"),
                "color": int(row.get("color") or 0),
                "hoist": bool(row.get("hoist", False)),
                "managed": bool(row.get("managed", False)),
                "mentionable": bool(row.get("mentionable", False)),
            })
        return result

    async def roles(self, guild_id: int) -> list[dict]:
        rows = await self.roles_detailed(guild_id)
        return [
            {"id": row["id"], "name": row["name"], "position": row["position"]}
            for row in rows
            if row["name"] != "@everyone"
        ]

    async def member(self, guild_id: int, user_id: int) -> dict:
        row = await self._get(f"/guilds/{guild_id}/members/{user_id}")
        user = row.get("user") or {}
        return {
            "id": str(user.get("id") or user_id),
            "username": str(user.get("username") or user_id),
            "global_name": user.get("global_name"),
            "display_name": row.get("nick") or user.get("global_name") or user.get("username") or str(user_id),
            "avatar": user.get("avatar"),
            "bot": bool(user.get("bot", False)),
            "roles": [str(value) for value in row.get("roles") or []],
            "joined_at": row.get("joined_at"),
            "premium_since": row.get("premium_since"),
            "pending": bool(row.get("pending", False)),
            "communication_disabled_until": row.get("communication_disabled_until"),
        }

    async def members(self, guild_id: int, *, limit: int = 1000) -> list[dict]:
        rows = await self._get(f"/guilds/{guild_id}/members?limit={max(1, min(1000, int(limit)))}")
        result: list[dict] = []
        for row in rows:
            user = row.get("user") or {}
            result.append({
                "id": str(user.get("id") or ""),
                "username": str(user.get("username") or ""),
                "display_name": row.get("nick") or user.get("global_name") or user.get("username") or "user",
                "bot": bool(user.get("bot", False)),
                "roles": [str(value) for value in row.get("roles") or []],
                "joined_at": row.get("joined_at"),
            })
        return result

    async def search_members(self, guild_id: int, query: str, *, limit: int = 50) -> list[dict]:
        clean = " ".join(query.strip().split())[:100]
        if not clean:
            return await self.members(guild_id, limit=min(limit, 100))
        rows = await self._get(f"/guilds/{guild_id}/members/search?query={quote(clean)}&limit={max(1, min(1000, int(limit)))}")
        result: list[dict] = []
        for row in rows:
            user = row.get("user") or {}
            result.append({
                "id": str(user.get("id") or ""),
                "username": str(user.get("username") or ""),
                "display_name": row.get("nick") or user.get("global_name") or user.get("username") or "user",
                "bot": bool(user.get("bot", False)),
                "roles": [str(value) for value in row.get("roles") or []],
                "joined_at": row.get("joined_at"),
            })
        return result

    async def patch_channel(self, channel_id: int, payload: dict) -> dict:
        allowed: dict = {}
        if "name" in payload:
            name = " ".join(str(payload["name"]).split()).strip()[:100]
            if not name:
                raise DiscordServiceError("Channel name cannot be empty.")
            allowed["name"] = name
        if "topic" in payload:
            allowed["topic"] = str(payload["topic"] or "")[:1024] or None
        if "rate_limit_per_user" in payload:
            allowed["rate_limit_per_user"] = max(0, min(21600, int(payload["rate_limit_per_user"] or 0)))
        if "nsfw" in payload:
            allowed["nsfw"] = bool(payload["nsfw"])
        if not allowed:
            raise DiscordServiceError("No supported channel changes supplied.")
        row = await self._request("PATCH", f"/channels/{channel_id}", payload=allowed)
        return {"id": str(row.get("id") or channel_id), "name": row.get("name"), "topic": row.get("topic"), "rate_limit_per_user": row.get("rate_limit_per_user"), "nsfw": row.get("nsfw")}

    async def patch_role(self, guild_id: int, role_id: int, payload: dict) -> dict:
        allowed: dict = {}
        if "name" in payload:
            name = " ".join(str(payload["name"]).split()).strip()[:100]
            if not name:
                raise DiscordServiceError("Role name cannot be empty.")
            allowed["name"] = name
        if "color" in payload:
            allowed["color"] = max(0, min(0xFFFFFF, int(payload["color"] or 0)))
        if "hoist" in payload:
            allowed["hoist"] = bool(payload["hoist"])
        if "mentionable" in payload:
            allowed["mentionable"] = bool(payload["mentionable"])
        if not allowed:
            raise DiscordServiceError("No supported role changes supplied.")
        row = await self._request("PATCH", f"/guilds/{guild_id}/roles/{role_id}", payload=allowed)
        return {"id": str(row.get("id") or role_id), "name": row.get("name"), "color": row.get("color"), "hoist": row.get("hoist"), "mentionable": row.get("mentionable")}
