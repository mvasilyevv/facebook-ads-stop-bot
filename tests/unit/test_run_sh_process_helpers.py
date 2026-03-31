from __future__ import annotations

import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUN_SH_PATH = PROJECT_ROOT / "run.sh"


def _run_shell_with_helpers(commands: str) -> subprocess.CompletedProcess[str]:
    """Запускает bash со вспомогательными функциями из run.sh."""
    helper_block = RUN_SH_PATH.read_text(encoding="utf-8").split("check_process_started()", 1)[0]
    script = f"{helper_block}\n{commands}\n"
    return subprocess.run(
        ["bash"],
        input=script,
        text=True,
        cwd=PROJECT_ROOT,
        capture_output=True,
        check=True,
    )


# Проверяем что helper добивает процесс через SIGKILL, если тот игнорирует SIGTERM.
def test_run_sh_stop_process_by_pid_escalates_to_sigkill():
    proc = subprocess.Popen(
        ["bash", "-lc", 'trap "" TERM; while :; do sleep 1; done'],
        cwd=PROJECT_ROOT,
    )
    try:
        _run_shell_with_helpers(f'stop_process_by_pid {proc.pid} "Тестовый процесс" 1')
        proc.wait(timeout=5)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


# Проверяем что helper удаляет stale PID-файл, если живого воркера по паттерну уже нет.
def test_run_sh_cleanup_singleton_pid_file_removes_stale_file(tmp_path: Path):
    pid_file = tmp_path / "fb_disable_worker.pid"
    pid_file.write_text("999999", encoding="utf-8")

    _run_shell_with_helpers(
        f'cleanup_singleton_pid_file "{pid_file}" "pattern-that-should-not-exist-for-tests"'
    )

    assert not pid_file.exists()
