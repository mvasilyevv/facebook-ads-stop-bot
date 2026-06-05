# -*- coding: utf-8 -*-
"""Контракт-тест жизненного цикла: каждый воркер supervisord.conf гасится в run.sh stop_all.

Анти-регресс: при добавлении нового [program:*] легко забыть terminate в stop_all —
тогда `./run.sh --down` оставит его жить при зависшем supervisord (как было с 7
воркерами, включая money-критичный cabinet_scheduler).
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]

# Программы с особым паттерном остановки (не run_*.py): node-сервис и uvicorn.
_SPECIAL = {"browser_agent", "api"}


def _supervisord_programs() -> list[str]:
    text = (_ROOT / "supervisord.conf").read_text(encoding="utf-8")
    return re.findall(r"^\[program:(\w+)\]", text, re.MULTILINE)


# Каждый [program:*] (кроме node/uvicorn) должен иметь run_<name>.py в stop_all/run.sh
def test_stop_all_covers_all_workers() -> None:
    runsh = (_ROOT / "run.sh").read_text(encoding="utf-8")
    missing = []
    for prog in _supervisord_programs():
        if prog in _SPECIAL:
            continue
        if f"run_{prog}.py" not in runsh:
            missing.append(prog)
    assert not missing, f"воркеры без terminate в run.sh stop_all: {missing}"


# api вынесен из supervisord (двойной uvicorn-фикс) — [program:api] не должен вернуться
def test_no_program_api_in_supervisord() -> None:
    assert "api" not in _supervisord_programs(), "[program:api] вернулся — снова двойной API"


# Каждый воркер с entrypoint run_*.py берёт singleton-lock (защита от двойного запуска)
def test_workers_acquire_singleton_lock() -> None:
    missing = []
    for prog in _supervisord_programs():
        if prog in _SPECIAL:
            continue
        run_file = _ROOT / f"run_{prog}.py"
        if not run_file.exists():
            continue
        if "acquire_singleton_lock" not in run_file.read_text(encoding="utf-8"):
            missing.append(prog)
    assert not missing, f"воркеры без acquire_singleton_lock: {missing}"
