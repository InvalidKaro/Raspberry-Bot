from __future__ import annotations

import ast
import os
from pathlib import Path


ALLOWED_EXTENSIONS = {
    ".py", ".md", ".txt", ".json", ".toml", ".yaml", ".yml",
    ".html", ".css", ".js", ".ini", ".cfg",
}
DENIED_PARTS = {".git", ".venv", "__pycache__", "data", "logs", "node_modules"}
MAX_FILE_BYTES = 512 * 1024


class EditorError(ValueError):
    pass


class EditorService:
    def __init__(self, repo_path: Path) -> None:
        self.repo_path = repo_path.resolve()

    def _resolve(self, relative: str) -> Path:
        relative = str(relative or "").strip().replace("\\", "/")
        if not relative or relative.startswith("/"):
            raise EditorError("Invalid path.")
        candidate = (self.repo_path / relative).resolve()
        try:
            candidate.relative_to(self.repo_path)
        except ValueError as exc:
            raise EditorError("Path leaves the repository.") from exc
        rel = candidate.relative_to(self.repo_path)
        if any(part in DENIED_PARTS or part.startswith(".env") for part in rel.parts):
            raise EditorError("This path is protected.")
        if candidate.suffix.lower() not in ALLOWED_EXTENSIONS:
            raise EditorError("This file type is not editable in the dashboard.")
        return candidate

    def list_files(self) -> list[dict]:
        result: list[dict] = []
        for base, dirs, filenames in os.walk(self.repo_path):
            base_path = Path(base)
            dirs[:] = [d for d in dirs if d not in DENIED_PARTS and not d.startswith(".env")]
            for name in filenames:
                path = base_path / name
                rel = path.relative_to(self.repo_path)
                if any(part.startswith(".env") for part in rel.parts):
                    continue
                if path.suffix.lower() not in ALLOWED_EXTENSIONS:
                    continue
                try:
                    size = path.stat().st_size
                except OSError:
                    continue
                if size <= MAX_FILE_BYTES:
                    result.append({"path": rel.as_posix(), "size": size})
                if len(result) >= 700:
                    return sorted(result, key=lambda item: item["path"].lower())
        return sorted(result, key=lambda item: item["path"].lower())

    def read(self, relative: str) -> dict:
        path = self._resolve(relative)
        if not path.is_file():
            raise EditorError("File not found.")
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            raise EditorError("File is too large for the web editor.")
        content = path.read_text(encoding="utf-8")
        return {"path": path.relative_to(self.repo_path).as_posix(), "content": content, "size": size}

    @staticmethod
    def validate(relative: str, content: str) -> dict:
        if len(content.encode("utf-8")) > MAX_FILE_BYTES:
            return {"ok": False, "message": "File exceeds the 512 KiB editor limit."}
        if relative.lower().endswith(".py"):
            try:
                ast.parse(content, filename=relative)
            except SyntaxError as exc:
                return {
                    "ok": False,
                    "message": f"Python syntax error: {exc.msg} (line {exc.lineno}, column {exc.offset})",
                    "line": exc.lineno,
                    "column": exc.offset,
                }
        return {"ok": True, "message": "Validation passed."}

    def save(self, relative: str, content: str) -> dict:
        path = self._resolve(relative)
        if not path.exists():
            raise EditorError("Creating new files is disabled in Phase 2; edit an existing file.")
        validation = self.validate(relative, content)
        if not validation["ok"]:
            return validation
        temp = path.with_name(f".{path.name}.dashboard-tmp")
        temp.write_text(content, encoding="utf-8")
        os.replace(temp, path)
        return {"ok": True, "message": f"Saved {relative}.", "path": relative}
