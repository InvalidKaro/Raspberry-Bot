from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Sequence


@dataclass(slots=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


async def run_command(
    args: Sequence[str],
    *,
    cwd: str | None = None,
    timeout: float = 20.0,
) -> CommandResult:
    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        await proc.communicate()
        return CommandResult(124, "", f"Command timed out after {timeout:.0f}s")

    return CommandResult(
        int(proc.returncode or 0),
        stdout_b.decode("utf-8", errors="replace").strip(),
        stderr_b.decode("utf-8", errors="replace").strip(),
    )
