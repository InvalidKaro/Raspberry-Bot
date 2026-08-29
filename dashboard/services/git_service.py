from __future__ import annotations

from pathlib import Path

from .commands import run_command


class GitService:
    def __init__(self, repo_path: Path) -> None:
        self.repo_path = repo_path

    async def status(self) -> dict:
        if not (self.repo_path / ".git").exists():
            return {"ok": False, "message": f"{self.repo_path} is not a Git repository."}
        cwd = str(self.repo_path)
        branch = await run_command(["git", "branch", "--show-current"], cwd=cwd, timeout=8)
        short = await run_command(["git", "status", "--short"], cwd=cwd, timeout=8)
        head = await run_command(["git", "log", "-1", "--pretty=%h|%s|%cr"], cwd=cwd, timeout=8)
        changes = [line for line in short.stdout.splitlines() if line.strip()]
        return {
            "ok": branch.ok and short.ok,
            "branch": branch.stdout or "unknown",
            "changes": changes[:100],
            "dirty": bool(changes),
            "last_commit": head.stdout,
        }

    async def pull(self) -> dict:
        result = await run_command(["git", "pull", "--ff-only"], cwd=str(self.repo_path), timeout=60)
        return {"ok": result.ok, "message": result.stdout or result.stderr or "git pull finished."}

    async def push(self) -> dict:
        result = await run_command(["git", "push"], cwd=str(self.repo_path), timeout=60)
        return {"ok": result.ok, "message": result.stdout or result.stderr or "git push finished."}
