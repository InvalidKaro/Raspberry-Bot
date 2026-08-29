from __future__ import annotations

import asyncio
import json
from pathlib import Path

from .commands import run_command
from .git_service import GitService


class DeployService:
    def __init__(self, repo_path: Path, bot_service: str) -> None:
        self.repo_path = repo_path
        self.bot_service = bot_service
        self.git = GitService(repo_path)
        self.state_dir = Path.home() / ".local" / "state" / "homepi-dashboard"
        self.state_file = self.state_dir / "deploy.json"

    async def _compile(self) -> dict:
        python = self.repo_path / ".venv" / "bin" / "python"
        if not python.is_file():
            return {"ok": False, "message": f"Python venv not found: {python}"}
        targets = ["bot.py", "config.py", "cogs", "database", "helpers", "modals", "services", "tasks", "views", "dashboard"]
        existing = [name for name in targets if (self.repo_path / name).exists()]
        result = await run_command(
            [str(python), "-m", "compileall", "-q", *existing],
            cwd=str(self.repo_path),
            timeout=45,
        )
        return {"ok": result.ok, "message": result.stderr or result.stdout or "Python compile check passed."}

    async def install_requirements(self) -> dict:
        python = self.repo_path / ".venv" / "bin" / "python"
        requirements = self.repo_path / "requirements.txt"
        if not python.is_file():
            return {"ok": False, "message": f"Python venv not found: {python}"}
        if not requirements.is_file():
            return {"ok": False, "message": "requirements.txt was not found."}
        result = await run_command(
            [str(python), "-m", "pip", "install", "-r", str(requirements)],
            cwd=str(self.repo_path),
            timeout=180,
        )
        output = result.stdout or result.stderr or "requirements installation finished."
        return {"ok": result.ok, "message": output[-12000:]}

    async def deploy(self) -> dict:
        status = await self.git.status()
        if status.get("dirty"):
            return {"ok": False, "message": "Commit your dashboard/code changes before deploying. Deploy only runs from a clean Git commit."}

        compile_result = await self._compile()
        if not compile_result["ok"]:
            return {"ok": False, "message": "Preflight failed:\n" + compile_result["message"]}

        current = await self.git.head_sha()
        rollback_sha = None
        if self.state_file.is_file():
            try:
                old = json.loads(self.state_file.read_text(encoding="utf-8"))
                rollback_sha = str(old.get("last_successful_sha") or "") or None
            except (OSError, ValueError, json.JSONDecodeError):
                rollback_sha = None
        if rollback_sha == current:
            rollback_sha = None
        if rollback_sha is None:
            rollback_sha = await self.git.parent_sha()

        result = await run_command(["sudo", "-n", "systemctl", "restart", self.bot_service], timeout=20)
        if not result.ok:
            return {"ok": False, "message": result.stderr or result.stdout or "Bot restart failed."}
        await asyncio.sleep(2.0)
        active = await run_command(["systemctl", "is-active", "--quiet", self.bot_service], timeout=5)

        self.state_dir.mkdir(parents=True, exist_ok=True)
        state = {"last_successful_sha": current if active.ok else None, "rollback_sha": rollback_sha}
        self.state_file.write_text(json.dumps(state), encoding="utf-8")

        if active.ok:
            return {"ok": True, "message": f"Deploy passed validation and the bot is running at {current[:10] if current else 'current HEAD'}."}

        logs = await run_command(["journalctl", "-u", self.bot_service, "-n", "35", "--no-pager"], timeout=8)
        return {
            "ok": False,
            "message": "Bot did not become active after restart.\n\n" + (logs.stdout or logs.stderr),
            "rollback_available": bool(rollback_sha),
        }

    async def rollback(self) -> dict:
        if not self.state_file.is_file():
            return {"ok": False, "message": "No dashboard deploy rollback point is stored."}
        try:
            state = json.loads(self.state_file.read_text(encoding="utf-8"))
            sha = str(state["rollback_sha"])
            if not sha or sha == "None":
                return {"ok": False, "message": "No previous deploy commit is available."}
        except (OSError, KeyError, ValueError, json.JSONDecodeError):
            return {"ok": False, "message": "Rollback state is invalid."}
        status = await self.git.status()
        if status.get("dirty"):
            return {"ok": False, "message": "Rollback refused: working tree has uncommitted changes."}
        reset = await self.git.hard_reset(sha)
        if not reset["ok"]:
            return reset
        restart = await run_command(["sudo", "-n", "systemctl", "restart", self.bot_service], timeout=20)
        if not restart.ok:
            return {"ok": False, "message": restart.stderr or restart.stdout or "Rollback restart failed."}
        await asyncio.sleep(2.0)
        active = await run_command(["systemctl", "is-active", "--quiet", self.bot_service], timeout=5)
        return {
            "ok": active.ok,
            "message": f"Rolled back to {sha[:10]} and restarted the bot." if active.ok else "Code rolled back, but the bot is still not active.",
        }
