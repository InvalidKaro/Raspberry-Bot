from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

from database.manager import Database
from dashboard import app_legacy
from dashboard.config import DashboardConfig


REPO_ROOT = Path(__file__).resolve().parents[1]


async def _prepare_database(path: Path) -> None:
    database = Database(path)
    await database.connect()
    await database.close()


async def _expect_status(response, expected: int, label: str) -> None:
    if response.status != expected:
        body = await response.text()
        raise AssertionError(f"{label}: expected HTTP {expected}, got {response.status}: {body[:500]}")


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="raspberry-dashboard-smoke-") as temp_name:
        temp = Path(temp_name)
        os.environ["HOME"] = str(temp / "home")
        Path(os.environ["HOME"]).mkdir(parents=True, exist_ok=True)

        database_path = temp / "bot.sqlite3"
        bot_env_path = temp / ".env"
        bot_env_path.write_text("DISCORD_TOKEN=smoke-test-token\n", encoding="utf-8")
        await _prepare_database(database_path)

        config = DashboardConfig(
            host="127.0.0.1",
            port=8080,
            dashboard_token="dashboard-smoke-token",
            dashboard_secret="dashboard-smoke-secret-at-least-24-chars",
            bot_service="raspberry-bot-smoke-test",
            repo_path=REPO_ROOT,
            bot_env_path=bot_env_path,
            database_path=database_path,
            log_lines=50,
            sample_interval_seconds=30,
        )

        # Creating the full wrapped app catches duplicate routes, missing imports,
        # schema bootstrap failures and extension-registration mistakes.
        app = app_legacy.create_app(config)
        server = TestServer(app)
        client = TestClient(server)
        await client.start_server()
        try:
            response = await client.get("/health")
            await _expect_status(response, 200, "health")
            payload = await response.json()
            assert payload.get("ok") is True, payload

            # Public status must really be public, not silently redirected to login.
            response = await client.get("/status", allow_redirects=False)
            await _expect_status(response, 200, "public status page")
            assert "status" in (await response.text()).lower()

            response = await client.get("/api/public/status", allow_redirects=False)
            await _expect_status(response, 200, "public status API")
            payload = await response.json()
            assert payload.get("ok") is True, payload
            assert payload.get("status") in {"operational", "degraded"}, payload

            # Protected pages must remain protected.
            response = await client.get("/ops", allow_redirects=False)
            await _expect_status(response, 302, "unauthenticated Dashboard Pro")
            assert response.headers.get("Location") == "/login", response.headers

            session_cookie = app_legacy._session_value(config)
            auth_headers = {"Cookie": f"dashboard_session={session_cookie}"}

            response = await client.get("/ops", headers=auth_headers)
            await _expect_status(response, 200, "authenticated Dashboard Pro")
            assert "dashboard" in (await response.text()).lower()

            response = await client.get("/now-playing", headers=auth_headers)
            await _expect_status(response, 200, "Now Playing page")

            # Exercise real API handlers against a freshly initialized SQLite DB.
            response = await client.get("/api/ops/summary", headers=auth_headers)
            await _expect_status(response, 200, "Dashboard Pro summary API")
            payload = await response.json()
            assert payload.get("ok") is True, payload
            assert isinstance(payload.get("health", {}).get("score"), int), payload

            response = await client.get("/api/ops/analytics?guild_id=1&days=7", headers=auth_headers)
            await _expect_status(response, 200, "analytics API")
            assert (await response.json()).get("ok") is True

            response = await client.get("/api/ops/features?guild_id=1", headers=auth_headers)
            await _expect_status(response, 200, "Feature Lab API")
            assert (await response.json()).get("ok") is True

            # Verify CSRF enforcement and one authenticated write path.
            response = await client.post(
                "/api/ops/widgets",
                headers=auth_headers,
                json={"guild_id": "1", "layout": []},
            )
            await _expect_status(response, 403, "CSRF protection")

            csrf_headers = {
                **auth_headers,
                "X-CSRF-Token": app_legacy._csrf_value(config),
            }
            response = await client.post(
                "/api/ops/widgets",
                headers=csrf_headers,
                json={"guild_id": "1", "layout": []},
            )
            await _expect_status(response, 200, "widget write API")
            assert (await response.json()).get("ok") is True

            print("Dashboard runtime smoke test passed")
        finally:
            await client.close()


if __name__ == "__main__":
    asyncio.run(main())
