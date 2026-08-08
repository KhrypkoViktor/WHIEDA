"""Subprocess helpers with captured output for E2E lab reporting."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from typing import Sequence


@dataclass
class StepResult:
    name: str
    command: list[str]
    returncode: int
    stdout: str = ""
    stderr: str = ""
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and self.error is None


def run_capture(
    cmd: Sequence[str],
    *,
    name: str,
    cwd: str | None = None,
    input_text: str | None = None,
    timeout: float | None = None,
) -> StepResult:
    try:
        proc = subprocess.run(
            list(cmd),
            cwd=cwd,
            input=input_text,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return StepResult(
            name=name,
            command=list(cmd),
            returncode=proc.returncode,
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
        )
    except subprocess.TimeoutExpired as exc:
        return StepResult(
            name=name,
            command=list(cmd),
            returncode=124,
            stdout=(exc.stdout or "") if isinstance(exc.stdout, str) else "",
            stderr=(exc.stderr or "") if isinstance(exc.stderr, str) else "",
            error=f"timeout after {timeout}s",
        )
    except OSError as exc:
        return StepResult(
            name=name,
            command=list(cmd),
            returncode=127,
            error=str(exc),
        )


def docker_logs(container: str, *, tail: int = 200) -> str:
    proc = subprocess.run(
        ["docker", "logs", "--tail", str(tail), container],
        capture_output=True,
        text=True,
        check=False,
    )
    combined = (proc.stdout or "") + (proc.stderr or "")
    return combined.strip()
