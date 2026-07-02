# -*- coding: utf-8 -*-
"""Персист created_ids partial-провала в task_queue.result (MID-24).

У 8 failed campaign_create (26-27.06) result был NULL — id осиротевших объектов
Meta жили только в ротируемых логах. Теперь PartialCreateError-ветка пишет их
и в task_queue.result (кроме campaign_run.created_meta_ids).
"""

from __future__ import annotations

import inspect
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.campaign_creator_worker.main import _persist_partial_created_ids


def _fake_engine():
    """engine.begin() как async context manager с мок-conn."""
    conn = AsyncMock()
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    engine = MagicMock()
    engine.begin = MagicMock(return_value=ctx)
    return engine, conn


# UPDATE уходит с guard status='running' и полным JSON (partial_fail/step/created_ids)
@pytest.mark.asyncio
async def test_persist_writes_result_json_with_running_guard():
    engine, conn = _fake_engine()
    created = {"campaigns": ["c1"], "adsets": ["s1", "s2"], "ads": []}

    await _persist_partial_created_ids(
        engine, task_id=42, created_ids=created, failed_step="creating"
    )

    conn.execute.assert_awaited_once()
    sql = str(conn.execute.await_args.args[0])
    params = conn.execute.await_args.args[1]
    assert "SET result" in sql
    assert "status = 'running'" in sql  # не затираем result чужой терминальной задачи
    assert params["id"] == 42
    payload = json.loads(params["r"])
    assert payload == {
        "partial_fail": True,
        "failed_step": "creating",
        "created_ids": created,
    }


# Сбой записи (БД упала) → warning, БЕЗ исключения наружу (mark_failed важнее)
@pytest.mark.asyncio
async def test_persist_failure_is_swallowed(caplog):
    engine = MagicMock()
    engine.begin = MagicMock(side_effect=ConnectionError("db down"))

    with caplog.at_level("WARNING"):
        await _persist_partial_created_ids(
            engine, task_id=7, created_ids={"campaigns": []}, failed_step="creating"
        )

    assert any("created_ids" in r.getMessage() for r in caplog.records)


# Контракт ветки PartialCreateError: персист зовётся ДО _safe_mark_failed
# (анти-регресс порядка — после mark_failed guard status='running' уже не пройдёт)
def test_partial_branch_persists_before_mark_failed():
    import apps.campaign_creator_worker.main as m

    src = inspect.getsource(m._execute_run)
    branch = src.split("except PartialCreateError")[1].split("except Exception")[0]
    assert "_persist_partial_created_ids" in branch
    assert branch.index("_persist_partial_created_ids") < branch.index("_safe_mark_failed")
