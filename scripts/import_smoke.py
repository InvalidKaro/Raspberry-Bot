from __future__ import annotations

import importlib
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SKIP = {"scripts.import_smoke"}
modules: list[str] = ["bot", "config"]

for package in ("cogs", "database", "helpers", "modals", "services", "tasks", "views"):
    package_path = ROOT / package
    for path in package_path.rglob("*.py"):
        if path.name == "__init__.py":
            module = ".".join(path.relative_to(ROOT).parent.parts)
        else:
            module = ".".join(path.relative_to(ROOT).with_suffix("").parts)
        if module and module not in modules and module not in SKIP:
            modules.append(module)

failed: list[tuple[str, Exception]] = []
for module in modules:
    try:
        importlib.import_module(module)
    except Exception as exc:  # noqa: BLE001 - smoke test should report every import failure
        failed.append((module, exc))

if failed:
    for module, exc in failed:
        print(f"FAIL {module}: {type(exc).__name__}: {exc}")
    raise SystemExit(1)

print(f"Imported {len(modules)} modules successfully.")
