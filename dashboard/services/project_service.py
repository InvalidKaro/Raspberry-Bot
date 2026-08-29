from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path


class ProjectService:
    def __init__(self, repo_path: Path) -> None:
        self.repo_path = repo_path

    def extensions(self) -> list[str]:
        path = self.repo_path / "bot.py"
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError):
            return []
        for node in tree.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if not any(isinstance(t, ast.Name) and t.id == "EXTENSIONS" for t in targets):
                    continue
                value = node.value
                if isinstance(value, (ast.Tuple, ast.List)):
                    rows = []
                    for item in value.elts:
                        if isinstance(item, ast.Constant) and isinstance(item.value, str):
                            rows.append(item.value)
                    return rows
        return []

    def commands(self) -> list[dict]:
        rows = []
        cogs = self.repo_path / "cogs"
        if not cogs.is_dir():
            return rows
        for path in sorted(cogs.rglob("*.py")):
            if path.name == "__init__.py" or "__pycache__" in path.parts:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except (OSError, SyntaxError):
                continue
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for deco in node.decorator_list:
                    call = deco if isinstance(deco, ast.Call) else None
                    func = call.func if call else deco
                    attr = func.attr if isinstance(func, ast.Attribute) else ""
                    if attr != "command":
                        continue
                    name = node.name
                    description = ""
                    if call:
                        for kw in call.keywords:
                            if kw.arg == "name" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                                name = kw.value.value
                            if kw.arg == "description" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                                description = kw.value.value
                    rel = path.relative_to(self.repo_path).as_posix()
                    rows.append({"name": name, "description": description, "file": rel, "function": node.name})
                    break
        return sorted(rows, key=lambda r: (r["file"], r["name"]))[:300]

    def overview(self) -> dict:
        extensions = self.extensions()
        commands = self.commands()
        categories = Counter(row["file"].split("/")[1] if row["file"].count("/") >= 2 else "other" for row in commands)
        missing = []
        for module in extensions:
            path = self.repo_path.joinpath(*module.split(".")).with_suffix(".py")
            if not path.is_file():
                missing.append(module)
        return {
            "ok": True,
            "extensions": extensions,
            "missing_extensions": missing,
            "commands": commands,
            "categories": dict(sorted(categories.items())),
        }
