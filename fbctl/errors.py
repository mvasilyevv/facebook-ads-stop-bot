"""Typed failures exposed by the fbctl command interface."""

from __future__ import annotations


class FbctlError(RuntimeError):
    """Expected operator-facing failure without a traceback or secret values."""

    def __init__(self, message: str, *, step: str | None = None, exit_code: int = 1) -> None:
        super().__init__(message)
        self.step = step
        self.exit_code = exit_code


class CommandFailed(FbctlError):
    """A subprocess failed at a named deployment step."""

    def __init__(self, command: tuple[str, ...], returncode: int, *, step: str) -> None:
        program = command[0] if command else "command"
        super().__init__(
            f"{program} exited with status {returncode}",
            step=step,
            exit_code=returncode or 1,
        )
        self.command = command
        self.returncode = returncode
