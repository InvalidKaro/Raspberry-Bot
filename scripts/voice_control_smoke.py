from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from dashboard.voice_routes import register_voice_routes


async def main() -> None:
    os.environ["VOICE_API_TOKEN"] = "voice-smoke-token-abcdefghijklmnopqrstuvwxyz"
    app = web.Application()
    register_voice_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        response = await client.post("/api/voice-command", json={"text": "HomePi Status"})
        assert response.status == 401, await response.text()

        headers = {"Authorization": "Bearer voice-smoke-token-abcdefghijklmnopqrstuvwxyz"}
        response = await client.post("/api/voice-command", headers=headers, json={"text": "HomePi Status"})
        assert response.status == 200, await response.text()
        payload = await response.json()
        assert payload.get("ok") is True, payload
        assert "CPU" in payload.get("speech", ""), payload

        response = await client.post("/api/voice-command", headers=headers, json={"text": "HomePi neu starten"})
        assert response.status == 409, await response.text()
        payload = await response.json()
        assert payload.get("confirmation_required") is True, payload
        assert payload.get("action") == "reboot", payload

        response = await client.post(
            "/api/voice-command",
            headers=headers,
            json={"text": "Dienst nginx neu starten"},
        )
        assert response.status == 409, await response.text()
        payload = await response.json()
        assert payload.get("confirmation_required") is True, payload
        assert payload.get("unit") == "nginx", payload

        response = await client.post(
            "/api/voice-command",
            headers=headers,
            json={"text": "Starte Meshtastic neu"},
        )
        # The known HomePi alias skips extra confirmation; on CI the privileged
        # helper is intentionally absent, so execution must fail safely rather
        # than ever falling back to a shell.
        assert response.status == 500, await response.text()
        payload = await response.json()
        assert payload.get("unit") == "raspberry-meshtastic", payload
        assert payload.get("action") == "restart", payload

        print("Voice control smoke test passed")
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
