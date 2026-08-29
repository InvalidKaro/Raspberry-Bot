from __future__ import annotations

import re
from pathlib import Path

from .commands import run_command
from .editor_service import EditorError, EditorService


class GitService:
    BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,79}$")

    def __init__(self, repo_path: Path) -> None:
        self.repo_path = repo_path
        self.editor = EditorService(repo_path)

    def _cwd(self) -> str:
        return str(self.repo_path)

    async def _branch(self) -> str:
        result = await run_command(["git", "branch", "--show-current"], cwd=self._cwd(), timeout=8)
        return result.stdout or "unknown"

    async def status(self) -> dict:
        if not (self.repo_path / ".git").exists():
            return {"ok": False, "message": f"{self.repo_path} is not a Git repository."}
        branch = await self._branch()
        short = await run_command(["git", "status", "--porcelain=v1", "--untracked-files=all"], cwd=self._cwd(), timeout=10)
        head = await run_command(["git", "log", "-1", "--pretty=%h|%s|%cr"], cwd=self._cwd(), timeout=8)
        changes = []
        for line in short.stdout.splitlines():
            if len(line) < 3:
                continue
            x, y = line[0], line[1]
            path = line[3:]
            if " -> " in path:
                path = path.split(" -> ", 1)[1]
            changes.append({
                "path": path,
                "code": (x + y).strip() or "?",
                "staged": x not in {" ", "?"},
                "unstaged": y != " " or x == "?",
                "untracked": x == "?",
            })
        upstream = await run_command(["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], cwd=self._cwd(), timeout=6)
        ahead = behind = None
        if upstream.ok and upstream.stdout:
            counts = await run_command(["git", "rev-list", "--left-right", "--count", f"{upstream.stdout}...HEAD"], cwd=self._cwd(), timeout=8)
            if counts.ok:
                try:
                    behind_s, ahead_s = counts.stdout.split()[:2]
                    behind, ahead = int(behind_s), int(ahead_s)
                except (ValueError, IndexError):
                    pass
        return {
            "ok": short.ok,
            "branch": branch,
            "changes": changes[:200],
            "dirty": bool(changes),
            "last_commit": head.stdout,
            "upstream": upstream.stdout if upstream.ok else None,
            "ahead": ahead,
            "behind": behind,
        }

    async def diff(self, path: str | None = None) -> dict:
        suffix: list[str] = []
        if path:
            try:
                resolved = self.editor._resolve(path, allow_directory=True)
                rel = resolved.relative_to(self.repo_path).as_posix()
            except EditorError as exc:
                return {"ok": False, "message": str(exc), "diff": ""}
            suffix = ["--", rel]
        unstaged = await run_command(["git", "diff", "--no-ext-diff", *suffix], cwd=self._cwd(), timeout=15)
        staged = await run_command(["git", "diff", "--cached", "--no-ext-diff", *suffix], cwd=self._cwd(), timeout=15)
        text = ""
        if staged.stdout:
            text += "# STAGED\n" + staged.stdout
        if unstaged.stdout:
            text += ("\n\n" if text else "") + "# UNSTAGED\n" + unstaged.stdout
        return {"ok": staged.ok and unstaged.ok, "diff": text[:300_000] or "No diff."}

    def _validated_paths(self, paths: list[str], *, require_existing: bool = False) -> list[str]:
        clean: list[str] = []
        for raw in paths[:100]:
            raw = str(raw or "").strip()
            if not raw:
                continue
            # Git may report a deleted file, so strict existence cannot always be required.
            if require_existing:
                path = self.editor._resolve(raw, allow_directory=True)
                rel = path.relative_to(self.repo_path).as_posix()
            else:
                parts = self.editor._relative_parts(raw)
                self.editor._check_parts(parts, allow_directory=True)
                candidate = self.repo_path.joinpath(*parts).resolve(strict=False)
                try:
                    candidate.relative_to(self.repo_path)
                except ValueError as exc:
                    raise EditorError(f"Invalid Git path: {raw}") from exc
                rel = Path(*parts).as_posix()
            if rel not in clean:
                clean.append(rel)
        if not clean:
            raise EditorError("Select at least one repository path.")
        return clean

    async def stage(self, paths: list[str]) -> dict:
        try:
            clean = self._validated_paths(paths)
        except EditorError as exc:
            return {"ok": False, "message": str(exc)}
        result = await run_command(["git", "add", "--", *clean], cwd=self._cwd(), timeout=20)
        return {"ok": result.ok, "message": result.stdout or result.stderr or "Selected paths staged."}

    async def unstage(self, paths: list[str]) -> dict:
        try:
            clean = self._validated_paths(paths)
        except EditorError as exc:
            return {"ok": False, "message": str(exc)}
        result = await run_command(["git", "restore", "--staged", "--", *clean], cwd=self._cwd(), timeout=20)
        if not result.ok:
            # Fallback works in repositories where restore --staged cannot resolve HEAD.
            result = await run_command(["git", "reset", "HEAD", "--", *clean], cwd=self._cwd(), timeout=20)
        return {"ok": result.ok, "message": result.stdout or result.stderr or "Selected paths unstaged."}

    async def discard(self, paths: list[str]) -> dict:
        status = await self.status()
        by_path = {row["path"]: row for row in status.get("changes", [])}
        try:
            clean = self._validated_paths(paths)
        except EditorError as exc:
            return {"ok": False, "message": str(exc)}
        untracked = [p for p in clean if by_path.get(p, {}).get("untracked")]
        if untracked:
            return {"ok": False, "message": "Untracked files are not auto-deleted by Git discard. Delete them from the Code file manager instead."}
        result = await run_command(["git", "restore", "--worktree", "--", *clean], cwd=self._cwd(), timeout=20)
        return {"ok": result.ok, "message": result.stdout or result.stderr or "Selected working-tree changes discarded."}

    async def commit(self, message: str, paths: list[str] | None = None) -> dict:
        message = " ".join(str(message or "").split()).strip()
        if len(message) < 3 or len(message) > 120:
            return {"ok": False, "message": "Commit message must contain 3–120 characters."}
        if paths:
            staged = await self.stage(paths)
            if not staged["ok"]:
                return staged
        commit = await run_command(["git", "commit", "-m", message], cwd=self._cwd(), timeout=30)
        return {"ok": commit.ok, "message": commit.stdout or commit.stderr or "git commit finished."}

    async def pull(self) -> dict:
        status = await self.status()
        if status.get("dirty"):
            return {"ok": False, "message": "Commit or discard local changes before pulling."}
        result = await run_command(["git", "pull", "--ff-only"], cwd=self._cwd(), timeout=60)
        return {"ok": result.ok, "message": result.stdout or result.stderr or "git pull finished."}

    async def push(self) -> dict:
        branch = await self._branch()
        upstream = await run_command(["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], cwd=self._cwd(), timeout=6)
        args = ["git", "push"] if upstream.ok else ["git", "push", "-u", "origin", branch]
        result = await run_command(args, cwd=self._cwd(), timeout=60)
        return {"ok": result.ok, "message": result.stdout or result.stderr or "git push finished."}

    async def branches(self) -> dict:
        result = await run_command(["git", "branch", "--format=%(refname:short)|%(HEAD)"], cwd=self._cwd(), timeout=10)
        rows = []
        for line in result.stdout.splitlines():
            name, _, head = line.partition("|")
            if name:
                rows.append({"name": name, "current": head.strip() == "*"})
        return {"ok": result.ok, "branches": rows, "message": result.stderr}

    async def create_branch(self, name: str) -> dict:
        name = str(name or "").strip()
        if not self.BRANCH_RE.fullmatch(name) or ".." in name or name.endswith("/"):
            return {"ok": False, "message": "Invalid branch name."}
        status = await self.status()
        if status.get("dirty"):
            return {"ok": False, "message": "Commit or discard changes before creating and switching branches."}
        result = await run_command(["git", "switch", "-c", name], cwd=self._cwd(), timeout=20)
        return {"ok": result.ok, "message": result.stdout or result.stderr or f"Switched to {name}."}

    async def switch_branch(self, name: str) -> dict:
        name = str(name or "").strip()
        if not self.BRANCH_RE.fullmatch(name):
            return {"ok": False, "message": "Invalid branch name."}
        status = await self.status()
        if status.get("dirty"):
            return {"ok": False, "message": "Commit or discard changes before switching branches."}
        result = await run_command(["git", "switch", name], cwd=self._cwd(), timeout=20)
        return {"ok": result.ok, "message": result.stdout or result.stderr or f"Switched to {name}."}

    async def history(self, limit: int = 30) -> dict:
        limit = max(1, min(80, int(limit)))
        result = await run_command([
            "git", "log", f"-{limit}", "--pretty=%h%x1f%H%x1f%an%x1f%ar%x1f%s"
        ], cwd=self._cwd(), timeout=12)
        commits = []
        for line in result.stdout.splitlines():
            parts = line.split("\x1f", 4)
            if len(parts) == 5:
                commits.append({"short": parts[0], "sha": parts[1], "author": parts[2], "when": parts[3], "subject": parts[4]})
        return {"ok": result.ok, "commits": commits, "message": result.stderr}

    async def head_sha(self) -> str | None:
        result = await run_command(["git", "rev-parse", "HEAD"], cwd=self._cwd(), timeout=8)
        return result.stdout if result.ok and result.stdout else None

    async def parent_sha(self) -> str | None:
        result = await run_command(["git", "rev-parse", "HEAD^"], cwd=self._cwd(), timeout=8)
        return result.stdout if result.ok and result.stdout else None

    async def hard_reset(self, sha: str) -> dict:
        if not re.fullmatch(r"[0-9a-fA-F]{7,40}", str(sha or "")):
            return {"ok": False, "message": "Invalid commit SHA."}
        result = await run_command(["git", "reset", "--hard", sha], cwd=self._cwd(), timeout=20)
        return {"ok": result.ok, "message": result.stdout or result.stderr or "Rollback complete."}
