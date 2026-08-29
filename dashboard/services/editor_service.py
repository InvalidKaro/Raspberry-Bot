from __future__ import annotations

import os
import re
import shutil
from pathlib import Path


class EditorError(RuntimeError):
    pass


class EditorService:
    EDITABLE_EXTENSIONS = {
        ".py", ".html", ".css", ".js", ".json", ".md", ".txt",
        ".toml", ".yaml", ".yml", ".sh", ".ini", ".cfg",
    }
    ALLOWED_SPECIAL_FILES = {".gitignore", ".env.example", ".env.dashboard.example"}
    BLOCKED_DIRS = {
        ".git", ".venv", "__pycache__", "data", "logs", "backups",
        ".idea", ".vscode", ".pytest_cache", ".mypy_cache",
    }
    BLOCKED_FILES = {".env", ".env.dashboard"}
    MAX_FILE_BYTES = 512 * 1024
    MAX_FILES = 800

    def __init__(self, repo_path: Path) -> None:
        self.repo_path = repo_path.resolve()

    def _relative_parts(self, relative: str) -> tuple[str, ...]:
        text = str(relative or "").replace("\\", "/").strip().strip("/")
        if not text:
            raise EditorError("A repository path is required.")
        if "\x00" in text:
            raise EditorError("Invalid path.")
        raw = Path(text)
        if raw.is_absolute() or ".." in raw.parts:
            raise EditorError("Path traversal is not allowed.")
        return raw.parts

    def _check_parts(self, parts: tuple[str, ...], *, allow_directory: bool = False) -> None:
        if any(part in self.BLOCKED_DIRS for part in parts):
            raise EditorError("That repository area is intentionally blocked in the dashboard.")
        if any(part in self.BLOCKED_FILES for part in parts):
            raise EditorError("Secret environment files cannot be opened in the dashboard.")
        if any(part.startswith(".env") and part not in self.ALLOWED_SPECIAL_FILES for part in parts):
            raise EditorError("Secret environment files cannot be opened in the dashboard.")
        if any(part.startswith(".") and part not in self.ALLOWED_SPECIAL_FILES for part in parts):
            raise EditorError("Hidden repository paths are blocked.")
        if not allow_directory:
            name = parts[-1]
            if name not in self.ALLOWED_SPECIAL_FILES and Path(name).suffix.lower() not in self.EDITABLE_EXTENSIONS:
                raise EditorError("This file type is not editable from the dashboard.")

    def _resolve(self, relative: str, *, allow_directory: bool = False, must_exist: bool = True) -> Path:
        parts = self._relative_parts(relative)
        self._check_parts(parts, allow_directory=allow_directory)
        path = self.repo_path.joinpath(*parts)
        try:
            resolved = path.resolve(strict=False)
            resolved.relative_to(self.repo_path)
        except (OSError, ValueError) as exc:
            raise EditorError("Path is outside the repository.") from exc
        if must_exist and not resolved.exists():
            raise EditorError("Path does not exist.")
        if resolved.is_symlink():
            raise EditorError("Symlinks cannot be edited from the dashboard.")
        return resolved

    def _editable_file(self, path: Path) -> bool:
        try:
            rel = path.relative_to(self.repo_path)
            self._check_parts(rel.parts, allow_directory=False)
        except (ValueError, EditorError):
            return False
        try:
            return path.is_file() and not path.is_symlink() and path.stat().st_size <= self.MAX_FILE_BYTES
        except OSError:
            return False

    def list_files(self) -> list[dict]:
        rows: list[dict] = []
        if not self.repo_path.is_dir():
            return rows
        for root, dirs, files in os.walk(self.repo_path):
            root_path = Path(root)
            dirs[:] = sorted(
                d for d in dirs
                if d not in self.BLOCKED_DIRS and not d.startswith(".")
            )
            rel_root = root_path.relative_to(self.repo_path)
            if rel_root.parts:
                rows.append({"path": rel_root.as_posix(), "kind": "dir"})
            for name in sorted(files):
                path = root_path / name
                if not self._editable_file(path):
                    continue
                rel = path.relative_to(self.repo_path).as_posix()
                try:
                    size = path.stat().st_size
                except OSError:
                    continue
                rows.append({"path": rel, "kind": "file", "size": size})
                if len(rows) >= self.MAX_FILES:
                    return rows
        return rows

    def read(self, relative: str) -> dict:
        path = self._resolve(relative)
        if not self._editable_file(path):
            raise EditorError("File is not editable or is too large.")
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise EditorError("Only UTF-8 text files can be edited.") from exc
        return {
            "path": path.relative_to(self.repo_path).as_posix(),
            "content": content,
            "size": len(content.encode("utf-8")),
        }

    def validate(self, relative: str, content: str) -> dict:
        parts = self._relative_parts(relative)
        self._check_parts(parts, allow_directory=False)
        size = len(content.encode("utf-8"))
        if size > self.MAX_FILE_BYTES:
            return {"ok": False, "message": "File exceeds the 512 KiB dashboard editor limit."}
        if Path(parts[-1]).suffix.lower() == ".py":
            try:
                compile(content, relative, "exec")
            except SyntaxError as exc:
                return {
                    "ok": False,
                    "message": f"Python syntax error: {exc.msg} (line {exc.lineno}, column {exc.offset})",
                    "line": exc.lineno,
                    "column": exc.offset,
                }
        if Path(parts[-1]).suffix.lower() == ".json":
            import json
            try:
                json.loads(content)
            except json.JSONDecodeError as exc:
                return {
                    "ok": False,
                    "message": f"JSON error: {exc.msg} (line {exc.lineno}, column {exc.colno})",
                    "line": exc.lineno,
                    "column": exc.colno,
                }
        return {"ok": True, "message": "Validation passed."}

    def save(self, relative: str, content: str) -> dict:
        path = self._resolve(relative)
        if not path.is_file():
            raise EditorError("Only files can be saved.")
        validation = self.validate(relative, content)
        if not validation["ok"]:
            return validation
        temp = path.with_name(f".{path.name}.dashboard-tmp")
        temp.write_text(content, encoding="utf-8")
        os.replace(temp, path)
        return {"ok": True, "message": f"Saved {relative}.", "path": relative}

    def create_file(self, relative: str, content: str = "") -> dict:
        path = self._resolve(relative, must_exist=False)
        if path.exists():
            raise EditorError("That path already exists.")
        if not path.parent.exists() or not path.parent.is_dir():
            raise EditorError("Parent directory does not exist.")
        validation = self.validate(relative, content)
        if not validation["ok"]:
            return validation
        path.write_text(content, encoding="utf-8")
        return {"ok": True, "message": f"Created {relative}.", "path": relative}

    def create_directory(self, relative: str) -> dict:
        path = self._resolve(relative, allow_directory=True, must_exist=False)
        if path.exists():
            raise EditorError("That path already exists.")
        if not path.parent.exists() or not path.parent.is_dir():
            raise EditorError("Parent directory does not exist.")
        path.mkdir()
        return {"ok": True, "message": f"Created directory {relative}.", "path": relative}

    def rename(self, old_relative: str, new_relative: str) -> dict:
        source = self._resolve(old_relative, allow_directory=True)
        is_dir = source.is_dir()
        target = self._resolve(new_relative, allow_directory=is_dir, must_exist=False)
        if target.exists():
            raise EditorError("Target path already exists.")
        if not target.parent.exists():
            raise EditorError("Target parent directory does not exist.")
        source.rename(target)
        return {
            "ok": True,
            "message": f"Renamed {old_relative} to {new_relative}.",
            "old_path": old_relative,
            "path": new_relative,
        }

    def delete(self, relative: str, *, recursive: bool = False) -> dict:
        path = self._resolve(relative, allow_directory=True)
        if path == self.repo_path:
            raise EditorError("Repository root cannot be deleted.")
        if path.is_dir():
            if recursive:
                count = sum(1 for _ in path.rglob("*"))
                if count > 200:
                    raise EditorError("Directory contains too many entries for dashboard deletion.")
                shutil.rmtree(path)
            else:
                try:
                    path.rmdir()
                except OSError as exc:
                    raise EditorError("Directory is not empty. Use recursive delete explicitly.") from exc
        else:
            path.unlink()
        return {"ok": True, "message": f"Deleted {relative}."}

    def search(self, query: str, *, limit: int = 60) -> dict:
        query = str(query or "").strip()
        if len(query) < 2:
            raise EditorError("Search query must contain at least two characters.")
        if len(query) > 100:
            raise EditorError("Search query is too long.")
        needle = query.casefold()
        results: list[dict] = []
        scanned = 0
        for item in self.list_files():
            if item.get("kind") != "file":
                continue
            scanned += 1
            if scanned > 500:
                break
            path = self._resolve(item["path"])
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for number, line in enumerate(lines, 1):
                if needle in line.casefold():
                    results.append({
                        "path": item["path"],
                        "line": number,
                        "preview": line.strip()[:220],
                    })
                    if len(results) >= max(1, min(100, limit)):
                        return {"ok": True, "results": results, "truncated": True}
        return {"ok": True, "results": results, "truncated": False}
