from __future__ import annotations

from pathlib import Path

from .commands import run_command
from .editor_service import EditorError, EditorService


class GitService:
    def __init__(self, repo_path: Path) -> None:
        self.repo_path = repo_path
        self.editor = EditorService(repo_path)

    def _cwd(self) -> str:
        return str(self.repo_path)

    async def status(self) -> dict:
        if not (self.repo_path / ".git").exists():
            return {"ok": False, "message": f"{self.repo_path} is not a Git repository."}
        branch = await run_command(["git", "branch", "--show-current"], cwd=self._cwd(), timeout=8)
        short = await run_command(["git", "status", "--short"], cwd=self._cwd(), timeout=8)
        head = await run_command(["git", "log", "-1", "--pretty=%h|%s|%cr"], cwd=self._cwd(), timeout=8)
        changes = [line for line in short.stdout.splitlines() if line.strip()]
        return {
            "ok": branch.ok and short.ok,
            "branch": branch.stdout or "unknown",
            "changes": changes[:150],
            "dirty": bool(changes),
            "last_commit": head.stdout,
        }

    async def diff(self) -> dict:
        unstaged = await run_command(["git", "diff", "--no-ext-diff", "--"], cwd=self._cwd(), timeout=12)
        staged = await run_command(["git", "diff", "--cached", "--no-ext-diff", "--"], cwd=self._cwd(), timeout=12)
        text = ""
        if staged.stdout:
            text += "# STAGED\n" + staged.stdout
        if unstaged.stdout:
            text += ("\n\n" if text else "") + "# UNSTAGED\n" + unstaged.stdout
        return {"ok": staged.ok and unstaged.ok, "diff": text[:250_000] or "No diff."}

    async def pull(self) -> dict:
        status = await self.status()
        if status.get("dirty"):
            return {"ok": False, "message": "Commit or discard local changes before pulling."}
        result = await run_command(["git", "pull", "--ff-only"], cwd=self._cwd(), timeout=60)
        return {"ok": result.ok, "message": result.stdout or result.stderr or "git pull finished."}

    async def push(self) -> dict:
        result = await run_command(["git", "push"], cwd=self._cwd(), timeout=60)
        return {"ok": result.ok, "message": result.stdout or result.stderr or "git push finished."}

    def _validated_paths(self, paths: list[str]) -> list[str]:
        clean: list[str] = []
        for raw in paths[:100]:
            try:
                path = self.editor._resolve(raw)
            except EditorError as exc:
                raise EditorError(f"Cannot stage {raw}: {exc}") from exc
            rel = path.relative_to(self.repo_path).as_posix()
            if rel not in clean:
                clean.append(rel)
        if not clean:
            raise EditorError("Select at least one file to commit.")
        return clean

    async def commit(self, message: str, paths: list[str]) -> dict:
        message = " ".join(str(message or "").split()).strip()
        if len(message) < 3 or len(message) > 120:
            return {"ok": False, "message": "Commit message must contain 3–120 characters."}
        try:
            clean = self._validated_paths(paths)
        except EditorError as exc:
            return {"ok": False, "message": str(exc)}

        add = await run_command(["git", "add", "--", *clean], cwd=self._cwd(), timeout=20)
        if not add.ok:
            return {"ok": False, "message": add.stderr or add.stdout or "git add failed."}
        commit = await run_command(["git", "commit", "-m", message], cwd=self._cwd(), timeout=30)
        return {"ok": commit.ok, "message": commit.stdout or commit.stderr or "git commit finished."}

    async def head_sha(self) -> str | None:
        result = await run_command(["git", "rev-parse", "HEAD"], cwd=self._cwd(), timeout=8)
        return result.stdout if result.ok and result.stdout else None

    async def parent_sha(self) -> str | None:
        result = await run_command(["git", "rev-parse", "HEAD^"], cwd=self._cwd(), timeout=8)
        return result.stdout if result.ok and result.stdout else None

    async def hard_reset(self, sha: str) -> dict:
        result = await run_command(["git", "reset", "--hard", sha], cwd=self._cwd(), timeout=20)
        return {"ok": result.ok, "message": result.stdout or result.stderr or "Rollback complete."}
