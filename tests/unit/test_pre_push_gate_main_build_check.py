from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GATE = ROOT / "scripts/pre-push-gate.sh"

FAKE_GH = """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$FAKE_GH_ARGS_LOG"
if [ "$1" = "run" ] && [ "$2" = "list" ]; then
  printf '%s' "${FAKE_GH_RUN_LIST_OUTPUT:-}"
  exit "${FAKE_GH_RUN_LIST_EXIT:-0}"
elif [ "$1" = "run" ] && [ "$2" = "view" ]; then
  printf '%s' "${FAKE_GH_RUN_VIEW_OUTPUT:-}"
  exit "${FAKE_GH_RUN_VIEW_EXIT:-0}"
fi
exit 1
"""


def _install_fake_gh(tmp_path: Path) -> tuple[Path, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_gh = bin_dir / "gh"
    fake_gh.write_text(FAKE_GH, encoding="utf-8")
    fake_gh.chmod(0o755)
    args_log = tmp_path / "gh-args.log"
    args_log.write_text("", encoding="utf-8")
    return bin_dir, args_log


def _run_check(
    tmp_path: Path, env_extra: dict[str, str]
) -> tuple[subprocess.CompletedProcess[str], str]:
    bin_dir, args_log = _install_fake_gh(tmp_path)
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["FAKE_GH_ARGS_LOG"] = str(args_log)
    env.update(env_extra)
    result = subprocess.run(
        ["bash", "-c", 'source "$1"; _gate_check_main_build', "pre-push-gate-test", str(GATE)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    return result, args_log.read_text(encoding="utf-8")


def test_red_manual_deploy_run_does_not_block_push(tmp_path: Path) -> None:
    # Ручной деплой (workflow_dispatch) может быть красным по причине, не
    # связанной с кодом (bootstrap чистого host) — гейт не должен его видеть
    # вовсе, поэтому проверяем, что запрос к gh уже отфильтрован по push.
    result, args_log = _run_check(
        tmp_path,
        {
            "FAKE_GH_RUN_LIST_OUTPUT": "success\thttps://example/runs/1\t1\tRelease",
        },
    )

    assert result.returncode == 0, result.stderr
    list_call = next(line for line in args_log.splitlines() if "list" in line)
    assert "--event" in list_call
    assert "push" in list_call


def test_red_push_build_blocks_and_names_run_and_job(tmp_path: Path) -> None:
    result, _ = _run_check(
        tmp_path,
        {
            "FAKE_GH_RUN_LIST_OUTPUT": ("failure\thttps://example/runs/42\t42\tRelease"),
            "FAKE_GH_RUN_VIEW_OUTPUT": "verify",
        },
    )

    assert result.returncode == 1
    assert "прогон проверок кода" in result.stderr
    assert "main красный" in result.stderr
    assert "https://example/runs/42" in result.stderr
    assert "verify" in result.stderr
    assert "Release" in result.stderr


def test_green_push_build_passes(tmp_path: Path) -> None:
    result, _ = _run_check(
        tmp_path,
        {
            "FAKE_GH_RUN_LIST_OUTPUT": "success\thttps://example/runs/2\t2\tRelease",
        },
    )

    assert result.returncode == 0, result.stderr


def test_unknown_build_state_does_not_block(tmp_path: Path) -> None:
    result, _ = _run_check(tmp_path, {"FAKE_GH_RUN_LIST_OUTPUT": ""})

    assert result.returncode == 0, result.stderr
    assert "неизвестно" in result.stderr
