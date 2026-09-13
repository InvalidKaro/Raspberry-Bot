from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True, slots=True)
class ProcessResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out


async def run_process(
    args: Sequence[str],
    *,
    cwd: str | None = None,
    timeout: float = 20.0,
) -> ProcessResult:
    """Run a fixed argv command without invoking a shell.

    Callers are responsible for allowlisting user-selectable executable arguments.
    The function bounds execution time and output decoding while avoiding shell
    expansion entirely.
    """

    if not args or not str(args[0]).strip():
        raise ValueError("run_process requires a non-empty argv sequence")
    if timeout <= 0:
        raise ValueError("timeout must be greater than zero")

    argv = tuple(str(item) for item in args)
    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        stdout_b, stderr_b = await proc.communicate()
        stderr = stderr_b.decode("utf-8", errors="replace").strip()
        timeout_message = f"Command timed out after {timeout:g}s"
        if stderr:
            timeout_message = f"{timeout_message}: {stderr[-500:]}"
        return ProcessResult(
            returncode=124,
            stdout=stdout_b.decode("utf-8", errors="replace").strip(),
            stderr=timeout_message,
            timed_out=True,
        )

    return ProcessResult(
        returncode=int(proc.returncode or 0),
        stdout=stdout_b.decode("utf-8", errors="replace").strip(),
        stderr=stderr_b.decode("utf-8", errors="replace").strip(),
    )
