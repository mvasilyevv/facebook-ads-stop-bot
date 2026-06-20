# -*- coding: utf-8 -*-
"""Unit: H-10 — воркеры используют консервативный пул соединений.

Дефолт SQLAlchemy (pool_size 5 + max_overflow 10 = 15/движок) × ~14 процессов ≈ 210
против Postgres max_connections=100 → «too many clients» под рестартами/нагрузкой.
make_worker_engine/WORKER_ENGINE_KWARGS даёт max 4/процесс. Контракт-тест следит,
чтобы воркеры не вернулись к голому create_async_engine без пула.
"""

from __future__ import annotations

import pathlib

import pytest

from core.db import WORKER_ENGINE_KWARGS, make_worker_engine

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

# Воркер-процессы + mcp, создающие собственный движок (НЕ через core.db.get_engine).
_WORKER_ENGINE_FILES = [
    "apps/health_watchdog/main.py",
    "apps/cabinet_scheduler/main.py",
    "apps/enable_recommendation_worker/main.py",
    "apps/tracker_aggregator_worker/main.py",
    "apps/creator_recorder/main.py",
    "apps/digest_scheduler/main.py",
    "apps/reconciler_worker/main.py",
    "apps/cleanup_worker/main.py",
    "apps/creator_worker/main.py",
    "apps/telegram_poller/main.py",
    "apps/meta_api_worker/main.py",
    "apps/mcp_server/context.py",
    "apps/observer_worker/main.py",
]


# Пул консервативный: суммарно по всем воркерам должно влезать в Postgres лимит.
def test_worker_engine_kwargs_conservative() -> None:
    assert WORKER_ENGINE_KWARGS["pool_size"] == 2
    assert WORKER_ENGINE_KWARGS["max_overflow"] == 2
    assert WORKER_ENGINE_KWARGS["pool_pre_ping"] is True
    # max 4/процесс × ~13 воркеров ≈ 52 + API(30) < 100 (с запасом на админ/миграции).
    assert WORKER_ENGINE_KWARGS["pool_size"] + WORKER_ENGINE_KWARGS["max_overflow"] <= 5


# make_worker_engine реально применяет pool_size.
def test_make_worker_engine_applies_pool() -> None:
    eng = make_worker_engine("postgresql+asyncpg://u:p@localhost:5433/db")
    assert eng.pool.size() == 2


# Контракт: каждый воркер-движок создаётся с **WORKER_ENGINE_KWARGS (а не голым echo=False).
@pytest.mark.parametrize("rel", _WORKER_ENGINE_FILES)
def test_worker_uses_pool_kwargs(rel: str) -> None:
    src = (_REPO_ROOT / rel).read_text(encoding="utf-8")
    if "create_async_engine(" not in src:
        pytest.skip(f"{rel}: нет create_async_engine")
    assert "**WORKER_ENGINE_KWARGS" in src, f"{rel}: движок без консервативного пула"
    assert "from core.db import WORKER_ENGINE_KWARGS" in src, f"{rel}: нет импорта пул-конфига"
    # Голый echo=False без пула не должен остаться.
    assert "create_async_engine(" in src
    for line in src.splitlines():
        if "create_async_engine(" in line and "WORKER_ENGINE_KWARGS" not in line:
            raise AssertionError(
                f"{rel}: create_async_engine без WORKER_ENGINE_KWARGS: {line.strip()}"
            )
