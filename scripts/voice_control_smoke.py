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

import dashboard.voice_routes as voice_routes


async def main() -> None:
    os.environ["VOICE_API_TOKEN"] = "voice-smoke-token-abcdefghijklmnopqrstuvwxyz"
    voice_routes._pending_confirmations.clear()

    executed: list[tuple[str, str | None]] = []

    async def fake_helper(action: str, unit: str | None = None, *, timeout: float = 30.0) -> dict:
        executed.append((action, unit))
        if action == "list":
            return {"ok": True, "output": "demo.service loaded active running Demo"}
        return {"ok": True, "output": "active" if action in {"status", "is-active", "is-enabled"} else "ok"}

    async def fake_delayed_helper(action: str, unit: str | None = None) -> None:
        executed.append((action, unit))

    voice_routes._helper = fake_helper
    voice_routes._delayed_helper = fake_delayed_helper

    app = web.Application()
    voice_routes.register_voice_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        response = await client.post("/api/voice-command", json={"text": "HomePi Status"})
        assert response.status == 401, await response.text()

        headers = {"Authorization": "Bearer voice-smoke-token-abcdefghijklmnopqrstuvwxyz"}

        response = await client.post("/api/voice-command", headers=headers, json={"text": "Home Pie Status"})
        assert response.status == 200, await response.text()
        payload = await response.json()
        assert payload.get("ok") is True, payload
        assert "CPU" in payload.get("speech", ""), payload
        assert "Systemcheck abgeschlossen" in payload.get("speech", ""), payload

        response = await client.post("/api/voice-command", headers=headers, json={"text": "Befehle"})
        assert response.status == 200, await response.text()
        payload = await response.json()
        assert payload.get("command") == "help", payload
        assert "commands" in payload, payload

        # Critical commands deliberately return HTTP 200 with a JSON prompt so
        # iOS Shortcuts can read and speak `speech` instead of treating the
        # confirmation as a transport error.
        response = await client.post("/api/voice-command", headers=headers, json={"text": "HomePi neu starten"})
        assert response.status == 200, await response.text()
        payload = await response.json()
        assert payload.get("confirmation_required") is True, payload
        assert payload.get("action") == "reboot", payload
        assert "Bestätigung" in payload.get("speech", ""), payload

        response = await client.post("/api/voice-command", headers=headers, json={"text": "Bestätigen"})
        assert response.status == 200, await response.text()
        payload = await response.json()
        assert payload.get("ok") is True, payload
        assert payload.get("action") == "reboot", payload
        assert "Bestätigt" in payload.get("speech", ""), payload
        await asyncio.sleep(0)
        assert ("reboot", None) in executed, executed

        response = await client.post("/api/voice-command", headers=headers, json={"text": "Pi herunterfahren"})
        assert response.status == 200, await response.text()
        payload = await response.json()
        assert payload.get("confirmation_required") is True, payload
        response = await client.post("/api/voice-command", headers=headers, json={"text": "Abbrechen"})
        assert response.status == 200, await response.text()
        payload = await response.json()
        assert payload.get("cancelled") is True, payload

        response = await client.post(
            "/api/voice-command",
            headers=headers,
            json={"text": "Dienst nginx neu starten"},
        )
        assert response.status == 200, await response.text()
        payload = await response.json()
        assert payload.get("confirmation_required") is True, payload
        assert payload.get("unit") == "nginx", payload

        response = await client.post("/api/voice-command", headers=headers, json={"text": "Befehl bestätigen"})
        assert response.status == 200, await response.text()
        payload = await response.json()
        assert payload.get("unit") == "nginx", payload
        assert payload.get("action") == "restart", payload
        assert ("restart", "nginx") in executed, executed

        response = await client.post(
            "/api/voice-command",
            headers=headers,
            json={"text": "Starte Meshtastic neu"},
        )
        assert response.status == 200, await response.text()
        payload = await response.json()
        assert payload.get("unit") == "raspberry-meshtastic", payload
        assert payload.get("action") == "restart", payload
        assert "Erledigt" in payload.get("speech", ""), payload
        assert "Meshtastic" in payload.get("speech", ""), payload
        assert ("restart", "raspberry-meshtastic") in executed, executed

        print("Voice control smoke test passed")
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
