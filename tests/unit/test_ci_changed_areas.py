# -*- coding: utf-8 -*-
"""Детектор изменений сдаётся в пользу лишнего прогона.

Пропуск проверки по ошибке дороже лишнего прогона: пропущенная проверка
означает, что дефект едет дальше молча. Поэтому любая неопределённость —
первый пуш ветки, force-push, недостижимая база — обязана давать «гоняем всё».
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = ROOT / "scripts/ci_changed_areas.py"

_ALL_KEYS = ("ui", "bundle")


def _run(*args: str) -> dict[str, str]:
    result = subprocess.run(
        [sys.executable, "-B", str(_SCRIPT), *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    parsed: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            parsed[key] = value
    return parsed


def test_assume_all_marks_every_area_changed() -> None:
    assert _run("--assume-all") == {key: "true" for key in _ALL_KEYS}


def test_unresolvable_base_falls_back_to_running_everything() -> None:
    # Первый пуш ветки приходит с базой из сорока нулей.
    assert _run("--base", "0" * 40, "--head", "HEAD") == {key: "true" for key in _ALL_KEYS}


def test_missing_base_falls_back_to_running_everything() -> None:
    assert _run("--base", "", "--head", "HEAD") == {key: "true" for key in _ALL_KEYS}


def test_every_key_is_always_printed() -> None:
    # Отсутствующий ключ в $GITHUB_OUTPUT даёт пустую строку, а пустая строка
    # не равна 'true' — то есть молчание детектора выключило бы проверку.
    printed = _run("--assume-all")
    assert sorted(printed) == sorted(_ALL_KEYS)


def test_documentation_only_change_touches_no_area() -> None:
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()
    assert _run("--base", head, "--head", head) == {key: "false" for key in _ALL_KEYS}
