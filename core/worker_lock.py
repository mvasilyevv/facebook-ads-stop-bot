# -*- coding: utf-8 -*-
"""Singleton file-lock для воркеров — защита от двойного запуска.

Два механизма запуска (supervisord + ручной `python run_*.py` / run.sh foreground)
могли поднять ДВА экземпляра одного воркера на одной очереди. `FOR UPDATE SKIP LOCKED`
спасал от двойного claim, но оба процесса жили: двойной poll, дублирующие TG-алерты
(health_watchdog), двойной cabinet_scheduler. Эксклюзивный fcntl-lock на файле в /tmp
гарантирует один экземпляр — второй процесс видит занятый lock и завершается с exit 0
(supervisord с exitcodes=0 не плодит рестарты).

Lock держится через открытый fd на всё время жизни процесса и освобождается ядром
при его смерти (в т.ч. SIGKILL) — stale-файлов не остаётся. Поэтому fd намеренно
не закрываем и держим ссылку в _HELD (иначе GC закроет fd и отпустит lock).
"""

from __future__ import annotations

import fcntl
import logging
import os
import sys
from pathlib import Path
from typing import IO

logger = logging.getLogger(__name__)

# worker_name → открытый fd с удержанным lock. Не давать GC закрыть fd.
_HELD: dict[str, IO[str]] = {}


def try_acquire(worker_name: str, *, lock_dir: str = "/tmp") -> bool:
    """Пытается взять эксклюзивный lock воркера. True — взят, False — уже занят.

    Не завершает процесс (для тестов и гибкого использования). Идемпотентен по
    worker_name в рамках процесса: если lock уже держится этим процессом —
    возвращает True (повторный захват того же fd не делается).
    """
    if worker_name in _HELD:
        return True
    lock_path = Path(lock_dir) / f"fb_agent_{worker_name}.lock"
    fd = open(lock_path, "a+")  # noqa: SIM115 — fd живёт всё время процесса намеренно
    try:
        fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fd.close()
        return False
    try:
        fd.seek(0)
        fd.truncate()
        fd.write(str(os.getpid()))
        fd.flush()
    except OSError:
        pass
    _HELD[worker_name] = fd
    return True


def acquire_singleton_lock(worker_name: str, *, lock_dir: str = "/tmp") -> None:
    """Берёт lock воркера; при занятом — завершает процесс (exit 0).

    Вызывать первой строкой в entrypoint воркера (run_*.py) до старта main_loop.
    exit 0 — нормальное завершение: дубликат уже работает, supervisord не рестартит.
    """
    if not try_acquire(worker_name, lock_dir=lock_dir):
        logger.warning("Воркер %s уже запущен (singleton-lock занят) — завершаю дубль", worker_name)
        sys.exit(0)


def release(worker_name: str) -> None:
    """Освобождает lock (закрывает fd). В основном для тестов — в проде lock живёт
    до смерти процесса и снимается ядром автоматически."""
    fd = _HELD.pop(worker_name, None)
    if fd is not None:
        try:
            fd.close()
        except OSError:
            pass


__all__ = ["acquire_singleton_lock", "release", "try_acquire"]
