"""Subprocess adapter used by the deployment module and its tests."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Protocol, Sequence

from fbctl.errors import CommandFailed


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


class CommandRunner(Protocol):
    def run(
        self,
        command: Sequence[str | Path],
        *,
        step: str,
        env: Mapping[str, str] | None = None,
        capture: bool = False,
        check: bool = True,
        input_text: str | None = None,
        timeout: float | None = None,
    ) -> CommandResult: ...


class SubprocessRunner:
    """Production adapter; never renders arguments into a shell command."""

    def run(
        self,
        command: Sequence[str | Path],
        *,
        step: str,
        env: Mapping[str, str] | None = None,
        capture: bool = False,
        check: bool = True,
        input_text: str | None = None,
        timeout: float | None = None,
    ) -> CommandResult:
        argv = tuple(os.fspath(part) for part in command)
        completed = subprocess.run(
            argv,
            check=False,
            text=True,
            input=input_text,
            capture_output=capture,
            env=dict(env) if env is not None else None,
            timeout=timeout,
        )
        result = CommandResult(completed.returncode, completed.stdout or "", completed.stderr or "")
        if check and result.returncode:
            raise CommandFailed(argv, result.returncode, step=step)
        return result


def sealed_process_environment(*, docker_config: Path | None = None) -> dict[str, str]:
    """Return the only host variables Docker needs for Compose interpolation."""
    environment = {
        "PATH": os.environ.get("PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin"),
        "HOME": os.environ.get("HOME", "/root"),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    if docker_config is not None:
        environment["DOCKER_CONFIG"] = os.fspath(docker_config)
    return environment
