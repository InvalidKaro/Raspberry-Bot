"""Dashboard control and private Unix IPC for opt-in WireGuard application proxy."""
from __future__ import annotations

import asyncio
import json
import os
import socket
import stat
import struct
from pathlib import Path

from aiohttp import web

from services.homepi_vpn_proxy import ProxyController, VPNError, socket_path

TEMPLATES = Path(__file__).resolve().parent / "templates"


async def page(_: web.Request) -> web.Response:
    return web.Response(
        text=(TEMPLATES / "vpn.html").read_text(encoding="utf-8"),
        content_type="text/html",
        headers={"Cache-Control": "no-store"},
    )


async def api_status(request: web.Request) -> web.Response:
    return web.json_response(await request.app["vpn_controller"].status(), headers={"Cache-Control": "no-store"})


async def api_action(request: web.Request) -> web.Response:
    # Existing app_legacy auth and CSRF middleware apply to this route.
    action = request.match_info["action"]
    try:
        if action == "connect":
            data = await request.json()
            if not isinstance(data, dict):
                raise VPNError("Invalid request.")
            result = await request.app["vpn_controller"].connect(data.get("profile"))
        elif action == "disconnect":
            result = await request.app["vpn_controller"].disconnect()
        else:
            return web.json_response({"ok": False, "message": "Unknown action."}, status=404)
    except (VPNError, ValueError) as exc:
        return web.json_response({"ok": False, "message": str(exc)}, status=400)
    except (OSError, asyncio.SubprocessError) as exc:
        return web.json_response({"ok": False, "message": "VPN proxy could not be started or stopped."}, status=503)
    return web.json_response(result, headers={"Cache-Control": "no-store"})


async def _handle_local(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, app: web.Application) -> None:
    try:
        peer = writer.get_extra_info("socket")
        if peer is None or not hasattr(socket, "SO_PEERCRED"):
            return
        credentials = peer.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
        _pid, uid, _gid = struct.unpack("3i", credentials)
        if uid != os.geteuid():
            return
        line = await asyncio.wait_for(reader.readline(), timeout=3)
        if len(line) > 2048:
            raise VPNError("Request too large.")
        data = json.loads(line)
        if not isinstance(data, dict):
            raise VPNError("Invalid request.")
        action = data.get("action")
        controller = app["vpn_controller"]
        if action == "status":
            result = await controller.status()
        elif action == "connect":
            result = await controller.connect(data.get("profile"))
        elif action == "disconnect":
            result = await controller.disconnect()
        else:
            raise VPNError("Unknown action.")
    except (VPNError, ValueError, asyncio.TimeoutError, OSError, json.JSONDecodeError) as exc:
        result = {"ok": False, "message": str(exc) if isinstance(exc, VPNError) else "VPN controller request failed."}
    except Exception:
        result = {"ok": False, "message": "VPN controller operation failed."}
    try:
        writer.write(json.dumps(result).encode("utf-8") + b"\n")
        await writer.drain()
    except OSError:
        pass
    finally:
        writer.close()
        await writer.wait_closed()


async def _start(app: web.Application) -> None:
    app["vpn_controller"] = ProxyController(Path(app["config"].repo_path))
    app["vpn_ipc_server"] = None
    app["vpn_ipc_path"] = None
    try:
        path = socket_path(Path(app["config"].repo_path))
        if path.is_symlink():
            return
        if path.exists():
            if not stat.S_ISSOCK(path.stat().st_mode) or path.stat().st_uid != os.geteuid():
                return
            try:
                _reader, writer = await asyncio.wait_for(asyncio.open_unix_connection(str(path)), timeout=0.5)
            except (OSError, asyncio.TimeoutError):
                path.unlink(missing_ok=True)  # stale socket belonging to current dashboard user
            else:
                writer.close()
                await writer.wait_closed()
                return  # an existing controller is listening; never steal its socket
        server = await asyncio.start_unix_server(
            lambda reader, writer: _handle_local(reader, writer, app),
            path=str(path),
            limit=2048,
        )
        os.chmod(path, 0o600)
        app["vpn_ipc_server"] = server
        app["vpn_ipc_path"] = path
    except (OSError, VPNError):
        # The web dashboard remains operational even if private Discord IPC cannot be created.
        pass


async def _stop(app: web.Application) -> None:
    server = app.get("vpn_ipc_server")
    if server is not None:
        server.close()
        await server.wait_closed()
    await app["vpn_controller"].close()
    path = app.get("vpn_ipc_path")
    if path is not None:
        path.unlink(missing_ok=True)


def register_vpn_routes(app: web.Application) -> None:
    app.on_startup.append(_start)
    app.on_cleanup.append(_stop)
    app.router.add_get("/vpn", page)
    app.router.add_get("/api/vpn/status", api_status)
    app.router.add_post("/api/vpn/{action:connect|disconnect}", api_action)
