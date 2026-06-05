# -*- coding: utf-8 -*-
"""Unit-тесты singleton file-lock воркеров (защита от двойного запуска)."""

from __future__ import annotations

import pytest

import core.worker_lock as wl


# Первый захват lock проходит, второй (другой fd того же файла) — отклоняется
def test_second_acquire_blocked(tmp_path) -> None:
    name = "t_block"
    wl.release(name)
    assert wl.try_acquire(name, lock_dir=str(tmp_path)) is True
    # имитируем второй процесс: убираем in-process запись (fd остаётся открыт и держит lock)
    held = wl._HELD.pop(name)
    try:
        assert wl.try_acquire(name, lock_dir=str(tmp_path)) is False
    finally:
        held.close()
        wl.release(name)


# acquire_singleton_lock завершает процесс (exit 0) при занятом lock
def test_acquire_exits_on_duplicate(tmp_path) -> None:
    name = "t_exit"
    wl.release(name)
    assert wl.try_acquire(name, lock_dir=str(tmp_path)) is True
    held = wl._HELD.pop(name)
    try:
        with pytest.raises(SystemExit) as exc:
            wl.acquire_singleton_lock(name, lock_dir=str(tmp_path))
        assert exc.value.code == 0
    finally:
        held.close()
        wl.release(name)


# Повторный захват тем же процессом (lock уже держится) → True (идемпотентно)
def test_reacquire_same_process_idempotent(tmp_path) -> None:
    name = "t_idem"
    wl.release(name)
    assert wl.try_acquire(name, lock_dir=str(tmp_path)) is True
    assert wl.try_acquire(name, lock_dir=str(tmp_path)) is True
    wl.release(name)


# release освобождает lock — после него захват снова возможен
def test_release_frees_lock(tmp_path) -> None:
    name = "t_rel"
    wl.release(name)
    assert wl.try_acquire(name, lock_dir=str(tmp_path)) is True
    wl.release(name)
    assert wl.try_acquire(name, lock_dir=str(tmp_path)) is True
    wl.release(name)
