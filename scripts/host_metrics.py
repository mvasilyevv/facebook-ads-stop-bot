#!/usr/bin/env python3
"""Atomic, root-owned Prometheus textfile metrics for host systemd jobs."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import stat
import tempfile
import time
from pathlib import Path
from typing import Any, Final, Literal

SCHEMA: Final = "fb-agent-host-operation/v2"
OPERATIONS: Final = frozenset(
    {
        "desktop_healer",
        "pgbackrest_diff",
        "pgbackrest_full",
        "release_boot_reconcile",
        "release_reconcile",
        "release_rollback",
        "restore_drill",
    }
)
OUTCOMES: Final = frozenset({"started", "success", "failure"})
Status = Literal["running", "success", "failure"]


class HostMetricsError(RuntimeError):
    """The host metric store violates a security or data invariant."""


def _validate_absolute(path: Path, *, name: str) -> Path:
    if not path.is_absolute() or ".." in path.parts:
        raise HostMetricsError(f"{name} must be an absolute path without '..'")
    return path


def _assert_secure_directory(
    path: Path,
    *,
    mode: int,
    expected_uid: int,
) -> None:
    if path.is_symlink():
        raise HostMetricsError(f"refusing symlinked directory: {path}")
    path.mkdir(parents=True, mode=mode, exist_ok=True)
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode):
        raise HostMetricsError(f"not a directory: {path}")
    if info.st_uid != expected_uid:
        raise HostMetricsError(f"directory is not owned by uid {expected_uid}: {path}")
    if stat.S_IMODE(info.st_mode) & 0o022:
        raise HostMetricsError(f"directory is group/world writable: {path}")
    os.chmod(path, mode)


def _assert_regular_file(path: Path, *, expected_uid: int) -> None:
    if path.is_symlink():
        raise HostMetricsError(f"refusing symlinked file: {path}")
    info = path.stat()
    if not stat.S_ISREG(info.st_mode):
        raise HostMetricsError(f"not a regular file: {path}")
    if info.st_uid != expected_uid:
        raise HostMetricsError(f"file is not owned by uid {expected_uid}: {path}")
    if stat.S_IMODE(info.st_mode) & 0o022:
        raise HostMetricsError(f"file is group/world writable: {path}")


def _atomic_write(
    path: Path,
    payload: bytes,
    *,
    mode: int,
    expected_uid: int,
) -> None:
    if path.exists() or path.is_symlink():
        _assert_regular_file(path, expected_uid=expected_uid)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        if os.geteuid() == 0:
            os.fchown(descriptor, expected_uid, 0)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _new_state(operation: str) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "operation": operation,
        "status": "failure",
        "last_start_timestamp_seconds": None,
        "last_completion_timestamp_seconds": None,
        "last_completion_boot_time_seconds": None,
        "last_success_timestamp_seconds": None,
        "last_failure_timestamp_seconds": None,
        "last_recovery_timestamp_seconds": None,
        "last_duration_seconds": None,
        "recovery_total": 0,
    }


def _validate_state(document: Any, *, operation: str) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise HostMetricsError(f"invalid state document for {operation}")
    if document.get("schema") != SCHEMA or document.get("operation") != operation:
        raise HostMetricsError(f"state identity mismatch for {operation}")
    if document.get("status") not in {"running", "success", "failure"}:
        raise HostMetricsError(f"invalid status for {operation}")
    for key in (
        "last_start_timestamp_seconds",
        "last_completion_timestamp_seconds",
        "last_completion_boot_time_seconds",
        "last_success_timestamp_seconds",
        "last_failure_timestamp_seconds",
        "last_recovery_timestamp_seconds",
        "last_duration_seconds",
    ):
        value = document.get(key)
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0
        ):
            raise HostMetricsError(f"invalid {key} for {operation}")
    recovery_total = document.get("recovery_total")
    if (
        isinstance(recovery_total, bool)
        or not isinstance(recovery_total, int)
        or recovery_total < 0
    ):
        raise HostMetricsError(f"invalid recovery_total for {operation}")
    return document


def _format_number(value: int | float) -> str:
    return format(float(value), ".6f").rstrip("0").rstrip(".") or "0"


def _current_boot_time_seconds() -> float:
    """Return the same Linux boot epoch used by node-exporter."""

    try:
        for line in Path("/proc/stat").read_text(encoding="ascii").splitlines():
            if line.startswith("btime "):
                value = int(line.removeprefix("btime ").strip())
                if value >= 0:
                    return float(value)
    except (OSError, UnicodeDecodeError, ValueError):
        pass
    # Non-Linux development fallback. Production hosts always use /proc/stat.
    return max(0.0, time.time() - time.monotonic())


class HostMetricStore:
    """Serializes operation state and one complete Prometheus textfile."""

    def __init__(
        self,
        *,
        output_dir: Path,
        state_dir: Path,
        enforce_root: bool = True,
    ) -> None:
        self.output_dir = _validate_absolute(output_dir, name="output directory")
        self.state_dir = _validate_absolute(state_dir, name="state directory")
        self.enforce_root = enforce_root
        current_uid = os.geteuid()
        if enforce_root and current_uid != 0:
            raise HostMetricsError("host metrics writer must run as root")
        self.expected_uid = 0 if enforce_root else current_uid

    @classmethod
    def from_environment(cls) -> HostMetricStore:
        return cls(
            output_dir=Path(
                os.environ.get(
                    "FB_AGENT_TEXTFILE_DIR",
                    "/var/lib/node_exporter/textfile_collector",
                )
            ),
            state_dir=Path(
                os.environ.get(
                    "FB_AGENT_HOST_METRICS_STATE_DIR",
                    "/var/lib/fb-agent/host-metrics",
                )
            ),
        )

    def record(
        self,
        operation: str,
        outcome: str,
        *,
        timestamp_seconds: float | None = None,
        boot_time_seconds: float | None = None,
    ) -> None:
        if operation not in OPERATIONS:
            raise HostMetricsError(f"unsupported operation: {operation}")
        if outcome not in OUTCOMES:
            raise HostMetricsError(f"unsupported outcome: {outcome}")
        timestamp = time.time() if timestamp_seconds is None else timestamp_seconds
        if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
            raise HostMetricsError("timestamp must be numeric")
        if timestamp < 0:
            raise HostMetricsError("timestamp must be non-negative")
        boot_time = _current_boot_time_seconds() if boot_time_seconds is None else boot_time_seconds
        if isinstance(boot_time, bool) or not isinstance(boot_time, (int, float)):
            raise HostMetricsError("boot time must be numeric")
        if boot_time < 0:
            raise HostMetricsError("boot time must be non-negative")

        _assert_secure_directory(
            self.state_dir,
            mode=0o700,
            expected_uid=self.expected_uid,
        )
        _assert_secure_directory(
            self.output_dir,
            mode=0o755,
            expected_uid=self.expected_uid,
        )
        lock_path = self.state_dir / ".lock"
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            if os.geteuid() == 0:
                os.fchown(lock_fd, self.expected_uid, 0)
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            state = self._read_state(operation)
            if outcome == "started":
                state["status"] = "running"
                state["last_start_timestamp_seconds"] = timestamp
            else:
                status: Status = "success" if outcome == "success" else "failure"
                last_failure = state["last_failure_timestamp_seconds"]
                last_success = state["last_success_timestamp_seconds"]
                recovering = (
                    status == "success"
                    and isinstance(last_failure, (int, float))
                    and (not isinstance(last_success, (int, float)) or last_failure > last_success)
                )
                state["status"] = status
                state["last_completion_timestamp_seconds"] = timestamp
                state["last_completion_boot_time_seconds"] = boot_time
                started = state["last_start_timestamp_seconds"]
                if isinstance(started, (int, float)):
                    state["last_duration_seconds"] = max(0.0, timestamp - started)
                if status == "success":
                    if recovering:
                        state["recovery_total"] += 1
                        state["last_recovery_timestamp_seconds"] = timestamp
                    state["last_success_timestamp_seconds"] = timestamp
                else:
                    state["last_failure_timestamp_seconds"] = timestamp
            self._write_state(operation, state)
            self._render_textfile()
        finally:
            os.close(lock_fd)

    def _state_path(self, operation: str) -> Path:
        return self.state_dir / f"{operation}.json"

    def _read_state(self, operation: str) -> dict[str, Any]:
        path = self._state_path(operation)
        if not path.exists() and not path.is_symlink():
            return _new_state(operation)
        _assert_regular_file(path, expected_uid=self.expected_uid)
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HostMetricsError(f"cannot read state for {operation}: {exc}") from exc
        return _validate_state(document, operation=operation)

    def _write_state(self, operation: str, state: dict[str, Any]) -> None:
        payload = (json.dumps(state, sort_keys=True, separators=(",", ":")) + "\n").encode()
        _atomic_write(
            self._state_path(operation),
            payload,
            mode=0o600,
            expected_uid=self.expected_uid,
        )

    def _render_textfile(self) -> None:
        states: list[dict[str, Any]] = []
        for operation in sorted(OPERATIONS):
            path = self._state_path(operation)
            if path.exists() or path.is_symlink():
                states.append(self._read_state(operation))
        headers = (
            (
                "fb_agent_host_operation_status",
                "Current host operation status: running=-1, failure=0, success=1.",
                "gauge",
            ),
            (
                "fb_agent_host_operation_last_start_timestamp_seconds",
                "Unix timestamp when the host operation last started.",
                "gauge",
            ),
            (
                "fb_agent_host_operation_last_completion_timestamp_seconds",
                "Unix timestamp when the host operation last completed.",
                "gauge",
            ),
            (
                "fb_agent_host_operation_last_completion_boot_time_seconds",
                "Host boot epoch for the host operation's last completion.",
                "gauge",
            ),
            (
                "fb_agent_host_operation_last_success_timestamp_seconds",
                "Unix timestamp of the host operation's last success.",
                "gauge",
            ),
            (
                "fb_agent_host_operation_last_failure_timestamp_seconds",
                "Unix timestamp of the host operation's last failure.",
                "gauge",
            ),
            (
                "fb_agent_host_operation_last_recovery_timestamp_seconds",
                "Unix timestamp when the host operation last recovered.",
                "gauge",
            ),
            (
                "fb_agent_host_operation_last_duration_seconds",
                "Duration of the host operation's last completed attempt.",
                "gauge",
            ),
            (
                "fb_agent_host_operation_recoveries_total",
                "Number of failure-to-success host operation recoveries.",
                "counter",
            ),
        )
        lines: list[str] = []
        for metric, help_text, metric_type in headers:
            lines.extend((f"# HELP {metric} {help_text}", f"# TYPE {metric} {metric_type}"))
            key = metric.removeprefix("fb_agent_host_operation_")
            for state in states:
                operation = state["operation"]
                if key == "status":
                    value: int | float = {"running": -1, "failure": 0, "success": 1}[
                        state["status"]
                    ]
                elif key == "recoveries_total":
                    value = state["recovery_total"]
                else:
                    state_key = key
                    value = state[state_key]
                    if value is None:
                        continue
                lines.append(f'{metric}{{operation="{operation}"}} {_format_number(value)}')
        payload = ("\n".join(lines) + "\n").encode()
        _atomic_write(
            self.output_dir / "fb-agent-host-operations.prom",
            payload,
            mode=0o644,
            expected_uid=self.expected_uid,
        )


def record_host_operation(operation: str, outcome: str) -> None:
    """Public helper used by release-state.py."""

    HostMetricStore.from_environment().record(operation, outcome)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    record = subparsers.add_parser("record")
    record.add_argument("--operation", required=True, choices=sorted(OPERATIONS))
    record.add_argument("--outcome", required=True, choices=sorted(OUTCOMES))
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "record":
        HostMetricStore.from_environment().record(args.operation, args.outcome)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
