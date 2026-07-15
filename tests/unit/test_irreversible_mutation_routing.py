# -*- coding: utf-8 -*-
"""Unit: маршрутизация ошибок необратимых mutations в meta_api_worker (C2).

Необратимые kinds (create_campaign/duplicate_campaign) создают новые объекты в Meta.
Если ответ потерян ПОСЛЕ коммита Meta (transient gRPC / битый JSON / ValueError на
постобработке / неклассифицированное), retry создал бы ДУБЛЬ кампании. Поэтому такие
ошибки уводятся в mark_failed (не requeue). Обратимые (pause_ad) — обычный requeue.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import apps.meta_api_worker.main as meta
from core.meta_api.errors import TemporaryError
from core.meta_api.mutations.duplicate_adset_structure import (
    DuplicateAdsetStructurePartialError,
)
from core.meta_api.mutations.duplicate_campaign import DuplicateCampaignPartialError


def _task(kind: str, tid: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        id=tid,
        task_type="meta_api_mutation",
        payload={"mutation_kind": kind, "target_id": "100"},
        attempt_count=0,
        max_attempts=5,
    )


@pytest.fixture
def _patched(monkeypatch):
    """Сканирование включено + owner-фильтр выключен → доходим до execute_mutation."""
    monkeypatch.setattr(meta, "load_scanning_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(meta, "load_owner_tag", AsyncMock(return_value=None))
    spy_fail = AsyncMock(return_value=True)
    spy_requeue = AsyncMock(return_value=True)
    monkeypatch.setattr(meta, "mark_task_failed", spy_fail)
    monkeypatch.setattr(meta, "requeue_task", spy_requeue)
    monkeypatch.setattr(meta, "notify_owners", AsyncMock(return_value=True))
    return spy_fail, spy_requeue


# create_campaign + TemporaryError (ответ мог потеряться после коммита) → mark_failed, НЕ requeue
@pytest.mark.asyncio
async def test_create_campaign_temporary_marks_failed(monkeypatch, _patched) -> None:
    spy_fail, spy_requeue = _patched
    monkeypatch.setattr(meta, "execute_mutation", AsyncMock(side_effect=TemporaryError("deadline")))
    await meta.process_one_task(object(), _task("create_campaign"), client=AsyncMock())
    spy_fail.assert_awaited_once()
    spy_requeue.assert_not_awaited()


# duplicate_campaign + ValueError (постобработка успешного ответа) → mark_failed, НЕ requeue
@pytest.mark.asyncio
async def test_duplicate_campaign_value_error_marks_failed(monkeypatch, _patched) -> None:
    spy_fail, spy_requeue = _patched
    monkeypatch.setattr(meta, "execute_mutation", AsyncMock(side_effect=ValueError("bad json")))
    await meta.process_one_task(object(), _task("duplicate_campaign"), client=AsyncMock())
    spy_fail.assert_awaited_once()
    spy_requeue.assert_not_awaited()


@pytest.mark.asyncio
async def test_duplicate_adset_structure_temporary_marks_failed(monkeypatch, _patched) -> None:
    spy_fail, spy_requeue = _patched
    monkeypatch.setattr(meta, "execute_mutation", AsyncMock(side_effect=TemporaryError("deadline")))
    await meta.process_one_task(object(), _task("duplicate_adset_structure"), client=AsyncMock())
    spy_fail.assert_awaited_once()
    spy_requeue.assert_not_awaited()


@pytest.mark.asyncio
async def test_duplicate_adset_structure_partial_routes_created_ids(monkeypatch, _patched) -> None:
    spy_fail, spy_requeue = _patched
    defer_recovery = AsyncMock(return_value=True)
    monkeypatch.setattr(meta, "defer_duplicate_recovery", defer_recovery)
    monkeypatch.setattr(
        meta,
        "execute_mutation",
        AsyncMock(
            side_effect=DuplicateAdsetStructurePartialError(
                "activation failed",
                created_ids={"campaigns": ["800"], "adsets": ["801"], "ads": ["802"]},
                failed_steps=[{"step": "activate_adset[801]", "error": "deadline"}],
                cleanup_failures=[{"id": "802", "error": "pause failed"}],
            )
        ),
    )
    await meta.process_one_task(object(), _task("duplicate_adset_structure"), client=AsyncMock())
    spy_fail.assert_not_awaited()
    spy_requeue.assert_not_awaited()
    defer_recovery.assert_awaited_once()
    error = defer_recovery.await_args.kwargs["error"]
    assert "duplicate_adset_structure_partial_fail" in error
    assert "800" in error and "801" in error and "802" in error
    assert defer_recovery.await_args.kwargs["checkpoint"] == {
        "checkpoint_type": "duplicate_adset_structure",
        "checkpoint_version": 1,
        "phase": "recovery_retrying",
        "partial_fail": True,
        "created_ids": {"campaigns": ["800"], "adsets": ["801"], "ads": ["802"]},
        "activated_ids": {"campaigns": [], "adsets": [], "ads": []},
        "failed_steps": [{"step": "activate_adset[801]", "error": "deadline"}],
        "cleanup_failures": [{"id": "802", "error": "pause failed"}],
        "recovery_requested": True,
    }
    notify = meta.notify_owners
    notify.assert_awaited_once()
    assert notify.await_args.kwargs["category"] == "critical"
    assert "800" in notify.await_args.kwargs["text"]
    assert "cleanup" in notify.await_args.kwargs["text"]


# create_campaign + неклассифицированный Exception → mark_failed, НЕ requeue
@pytest.mark.asyncio
async def test_create_campaign_unknown_exception_marks_failed(monkeypatch, _patched) -> None:
    spy_fail, spy_requeue = _patched
    monkeypatch.setattr(meta, "execute_mutation", AsyncMock(side_effect=RuntimeError("boom")))
    await meta.process_one_task(object(), _task("create_campaign"), client=AsyncMock())
    spy_fail.assert_awaited_once()
    spy_requeue.assert_not_awaited()


# Контраст: pause_ad (обратимая) + TemporaryError → обычный requeue, НЕ mark_failed
@pytest.mark.asyncio
async def test_pause_ad_temporary_requeues(monkeypatch, _patched) -> None:
    spy_fail, spy_requeue = _patched
    monkeypatch.setattr(meta, "execute_mutation", AsyncMock(side_effect=TemporaryError("deadline")))
    await meta.process_one_task(object(), _task("pause_ad"), client=AsyncMock())
    spy_requeue.assert_awaited_once()
    spy_fail.assert_not_awaited()


# MID-4: duplicate_campaign copy ok + rename fail → DuplicateCampaignPartialError →
# mark_failed БЕЗ retry (retry создал бы вторую копию), осиротевший id в error mark_failed.
@pytest.mark.asyncio
async def test_duplicate_campaign_partial_error_marks_failed_with_orphan_id(
    monkeypatch, _patched
) -> None:
    spy_fail, spy_requeue = _patched
    monkeypatch.setattr(
        meta,
        "execute_mutation",
        AsyncMock(
            side_effect=DuplicateCampaignPartialError(
                "копия создана (id=888), но переименование не удалось: http 400",
                created_ids={"campaign": "888"},
                failed_steps=[{"step": "rename", "error": "http 400"}],
            )
        ),
    )
    await meta.process_one_task(object(), _task("duplicate_campaign"), client=AsyncMock())
    # Помечена failed без retry — контракт как у CreateCampaignPartialError.
    spy_fail.assert_awaited_once()
    spy_requeue.assert_not_awaited()
    # Осиротевший id доехал в error mark_failed (для ручной чистки).
    err = spy_fail.await_args.kwargs["error"]
    assert "888" in err
    assert "duplicate_partial_fail" in err


# ─── Аудит 2026-07-12 (M-2): pre-send ошибки ретраятся даже для необратимых ──


# SessionUnavailableError (circuit-open / Vision не готов) = запрос НЕ ушёл в Meta →
# retry безопасен даже для create_campaign; раньше блип канала навсегда убивал залив.
@pytest.mark.asyncio
async def test_create_campaign_session_unavailable_requeues(monkeypatch, _patched) -> None:
    from core.meta_api.errors import SessionUnavailableError

    spy_fail, spy_requeue = _patched
    monkeypatch.setattr(
        meta,
        "execute_mutation",
        AsyncMock(side_effect=SessionUnavailableError("browser-agent недоступен")),
    )
    await meta.process_one_task(object(), _task("create_campaign"), client=AsyncMock())
    spy_requeue.assert_awaited_once()
    spy_fail.assert_not_awaited()


# Контраст: mid-flight TemporaryError (-2 Failed to fetch / DEADLINE) для необратимой —
# по-прежнему mark_failed (ответ мог потеряться ПОСЛЕ коммита Meta).
@pytest.mark.asyncio
async def test_duplicate_campaign_session_unavailable_requeues(monkeypatch, _patched) -> None:
    from core.meta_api.errors import SessionUnavailableError

    spy_fail, spy_requeue = _patched
    monkeypatch.setattr(
        meta,
        "execute_mutation",
        AsyncMock(side_effect=SessionUnavailableError("Vision-сессия не готова")),
    )
    await meta.process_one_task(object(), _task("duplicate_campaign"), client=AsyncMock())
    spy_requeue.assert_awaited_once()
    spy_fail.assert_not_awaited()


# M-2 (сквозной): create_campaign бросил NothingCommittedError (handler доказал —
# в Meta ничего не создано) → worker делает requeue, а не _fail_irreversible.
# Голый Temporary для irreversible по-прежнему уходит в mark_failed (тест выше).
@pytest.mark.asyncio
async def test_create_campaign_nothing_committed_requeues(monkeypatch, _patched) -> None:
    from core.meta_api.errors import NothingCommittedError

    spy_fail, spy_requeue = _patched
    monkeypatch.setattr(
        meta,
        "execute_mutation",
        AsyncMock(side_effect=NothingCommittedError("rate limit, ничего не создано")),
    )
    await meta.process_one_task(object(), _task("create_campaign"), client=AsyncMock())
    spy_requeue.assert_awaited_once()
    spy_fail.assert_not_awaited()
