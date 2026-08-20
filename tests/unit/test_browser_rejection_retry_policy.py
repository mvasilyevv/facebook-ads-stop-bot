# -*- coding: utf-8 -*-
"""Unit (#211): исход отказа браузера и политика его повтора — разные вопросы.

browser-agent отвергает операцию сам, до отправки в Meta, и называет причину.
Исход у всей семьи один и не обсуждается: ``REJECTED`` — отправки не было,
объектов не создано, сверять оператору нечего.

Политика повтора у той же семьи разная и задаётся кодом причины:

- истёкший грант, недоступный сервис выдачи разрешений — повтор осмыслен,
  задача возвращается в очередь;
- неверная подпись, чужой кабинет, неавторизованный вызывающий, сломанная
  семантика Graph-запроса, кабинет не числом — повтором той же задачи не
  лечится, и вечный requeue money-задачи прячет поломку вызывающего вместо
  того, чтобы её показать.

Проверяется наблюдаемое поведение воркеров обеих полос: чем закончилась задача
(финализирована или вернулась в очередь) и увидит ли оператор причину.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import apps.meta_api_worker.main as meta
import core.campaign_builder.execute as campaign_execute
from core.meta_api.errors import (
    BROWSER_OPERATION_PERMANENT_REJECTIONS,
    BROWSER_OPERATION_REJECTION_REASONS,
    BrowserOperationRejectedError,
    BrowserReadinessRejectedError,
)
from core.tasks.action_reason import operator_reason_from_result
from core.tasks.queue import Task

# Причина, которую повтор не лечит: подпись разрешения не сошлась.
_UNRETRYABLE_REASON = "capability_signature_invalid"
# Причина той же семьи, которую повтор лечит: грант истёк, следующий будет свежим.
_RETRYABLE_REASON = "capability_expired"


def _rejection(reason_code: str) -> BrowserOperationRejectedError:
    """Отказ ровно в той форме, в какой его строит клиент Marketing API."""
    return BrowserOperationRejectedError(
        "browser-agent отверг операцию до отправки в Meta: "
        f"{BROWSER_OPERATION_REJECTION_REASONS[reason_code]}",
        reason_code=reason_code,
        endpoint="/act_456/ads",
    )


# Каждая неисправимая причина обязана иметь человеческий текст: иначе оператор
# получит задачу, которая больше не повторится, и «Причина не записана».
def test_every_unretryable_reason_has_operator_wording() -> None:
    assert BROWSER_OPERATION_PERMANENT_REJECTIONS
    assert BROWSER_OPERATION_PERMANENT_REJECTIONS <= set(BROWSER_OPERATION_REJECTION_REASONS)


# ====================== money-полоса: apps/meta_api_worker ======================


def _money_task(**over) -> Task:
    now = datetime.now(UTC)
    base = dict(
        id=211,
        task_type="meta_api_mutation",
        status="running",
        idempotency_key="meta:pause_ad:211",
        payload={"mutation_kind": "pause_ad", "target_id": "123", "ad_account_id": "456"},
        attempt_count=0,
        max_attempts=72,
        requested_by="bot_auto_stop",
        last_error=None,
        created_at=now,
        external_started_at=None,
        result=None,
        lane="money",
        priority=100,
        available_at=now,
        deadline_at=now + timedelta(seconds=30),
        lease_owner=uuid.UUID("00000000-0000-0000-0000-000000000211"),
        lease_token=3,
        lease_expires_at=now + timedelta(minutes=1),
        cancel_requested_at=None,
        cancel_reason=None,
        correlation_id=uuid.uuid4(),
    )
    base.update(over)
    return Task(**base)


@pytest.fixture
def money_worker(monkeypatch):
    """Воркер доходит до внешнего вызова; все финализаторы под наблюдением."""
    monkeypatch.setattr(meta, "load_scanning_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(meta, "load_owner_tag", AsyncMock(return_value=None))
    monkeypatch.setattr(
        meta,
        "load_meta_snapshot_freshness",
        AsyncMock(return_value=SimpleNamespace(fresh=True)),
    )
    monkeypatch.setattr(meta, "_preflight_task_control", AsyncMock(return_value=None))
    monkeypatch.setattr(meta, "mark_external_call_started", AsyncMock(return_value=True))
    spies = SimpleNamespace(
        failed=AsyncMock(return_value=True),
        requeue=AsyncMock(return_value=True),
        requeue_pre_send=AsyncMock(return_value="retrying"),
        release_readiness=AsyncMock(return_value="retrying"),
        alert=AsyncMock(return_value=True),
    )
    monkeypatch.setattr(meta, "mark_task_failed", spies.failed)
    monkeypatch.setattr(meta, "requeue_task", spies.requeue)
    monkeypatch.setattr(meta, "requeue_task_proven_not_committed", spies.requeue_pre_send)
    monkeypatch.setattr(
        meta,
        "release_task_after_browser_readiness_rejection",
        spies.release_readiness,
    )
    monkeypatch.setattr(meta, "maybe_alert_autostop_channel_down", spies.alert)
    return spies


@pytest.mark.asyncio
async def test_money_task_with_unretryable_rejection_is_finalized_rejected(
    monkeypatch, money_worker
) -> None:
    monkeypatch.setattr(
        meta,
        "execute_mutation",
        AsyncMock(side_effect=_rejection(_UNRETRYABLE_REASON)),
    )

    await meta.process_one_task(
        object(),
        _money_task(),
        client=AsyncMock(),
        alert_ctx=meta.AutostopAlertContext(engine=object()),
    )

    # Повторов больше нет — ни обычного, ни доказанного pre-send.
    money_worker.requeue.assert_not_awaited()
    money_worker.requeue_pre_send.assert_not_awaited()
    money_worker.failed.assert_awaited_once()
    result = money_worker.failed.await_args.kwargs["result"]
    # Исход не изменился: отправки не было, сверять нечего.
    assert result["outcome"] == "REJECTED"
    assert result.get("reconcile_required") is not True
    assert result.get("manual_review_required") is not True
    # Причина доезжает до карточки оператора, а не только до лога.
    assert operator_reason_from_result(result) is not None


# Money-сигнал не исчезает вместе с повторами: команда «выключить» не дошла до
# кабинета и больше не дойдёт — объявление продолжает тратить бюджет.
@pytest.mark.asyncio
async def test_money_task_with_unretryable_rejection_still_signals_undelivered_stop(
    monkeypatch, money_worker
) -> None:
    monkeypatch.setattr(
        meta,
        "execute_mutation",
        AsyncMock(side_effect=_rejection(_UNRETRYABLE_REASON)),
    )

    await meta.process_one_task(
        object(),
        _money_task(),
        client=AsyncMock(),
        alert_ctx=meta.AutostopAlertContext(engine=object()),
    )

    money_worker.alert.assert_awaited_once()
    assert money_worker.alert.await_args.kwargs["fb_ad_id"] == "123"


@pytest.mark.asyncio
async def test_money_task_with_retryable_rejection_returns_to_queue(
    monkeypatch, money_worker
) -> None:
    monkeypatch.setattr(
        meta,
        "execute_mutation",
        AsyncMock(side_effect=_rejection(_RETRYABLE_REASON)),
    )

    await meta.process_one_task(
        object(),
        _money_task(),
        client=AsyncMock(),
        alert_ctx=meta.AutostopAlertContext(engine=object()),
    )

    money_worker.requeue_pre_send.assert_awaited_once()
    money_worker.failed.assert_not_awaited()


# Отказ готовности канала остаётся отдельной семьёй: задача возвращается в
# очередь, не сжигая попытку. Политика повтора по коду причины его не касается.
@pytest.mark.asyncio
async def test_money_task_with_readiness_rejection_is_released_without_burn(
    monkeypatch, money_worker
) -> None:
    monkeypatch.setattr(
        meta,
        "execute_mutation",
        AsyncMock(side_effect=BrowserReadinessRejectedError("channel is not ready")),
    )

    await meta.process_one_task(
        object(),
        _money_task(),
        client=AsyncMock(),
        alert_ctx=meta.AutostopAlertContext(engine=object()),
    )

    money_worker.release_readiness.assert_awaited_once()
    money_worker.failed.assert_not_awaited()
    money_worker.requeue_pre_send.assert_not_awaited()


# ====================== bulk-полоса: apps/campaign_creator_worker ======================


class _UnitControl:
    def __init__(self, **kwargs) -> None:
        self.external_started = False

    async def check(self) -> None:
        return None

    async def begin_external(self, _operation: str) -> None:
        self.external_started = True


def _creator_task() -> Task:
    now = datetime.now(UTC)
    return Task(
        id=212,
        task_type="campaign_create",
        status="running",
        idempotency_key="campaign:run-211",
        payload={"run_id": "run-211"},
        attempt_count=0,
        max_attempts=5,
        requested_by="operator",
        last_error=None,
        created_at=now,
        external_started_at=None,
        result=None,
        lane="bulk",
        priority=0,
        available_at=now,
        deadline_at=now + timedelta(minutes=30),
        lease_owner=uuid.UUID("00000000-0000-0000-0000-000000000212"),
        lease_token=4,
        lease_expires_at=now + timedelta(minutes=30),
        cancel_requested_at=None,
        cancel_reason=None,
        correlation_id=uuid.uuid4(),
    )


async def _run_creator_until_rejection(monkeypatch, *, reason_code: str):
    """Гоняет залив до отказа браузера, завёрнутого ровно как в бою."""
    import apps.campaign_creator_worker.main as worker

    async def _execute(*_args, **_kwargs):
        # Обёртку строит production-код: воркер видит CampaignExecutionError,
        # а названная причина живёт в цепочке __cause__.
        campaign_execute._raise_for_failure(  # noqa: SLF001
            {"campaigns": [], "adsets": [], "creatives": [], "ads": []},
            _rejection(reason_code),
            failed_step="creating_campaign",
        )

    async def _direct(_control, operation_factory):
        return await operation_factory()

    cfg = SimpleNamespace(account=SimpleNamespace(act_id="act_456"))
    monkeypatch.setattr(worker, "CreatorTaskControl", _UnitControl)
    monkeypatch.setattr(worker, "parse_run_config", lambda _cfg: cfg)
    monkeypatch.setattr(worker, "resolve_concepts_from_config", lambda _cfg: {})
    monkeypatch.setattr(worker, "build_campaign_spec", lambda _cfg: object())
    monkeypatch.setattr(worker, "set_run_status", AsyncMock(return_value=True))
    monkeypatch.setattr(worker, "execute_campaign_spec", _execute)
    monkeypatch.setattr(worker, "run_with_task_control", _direct)
    spies = SimpleNamespace(
        failed=AsyncMock(return_value=True),
        retry=AsyncMock(return_value=True),
    )
    monkeypatch.setattr(worker, "finalize_run_failed", spies.failed)
    monkeypatch.setattr(worker, "requeue_for_retry", spies.retry)

    await worker._execute_run(  # noqa: SLF001
        object(),
        _creator_task(),
        run_id="run-211",
        config={},
        client=AsyncMock(),
        uploader=AsyncMock(),
        control=_UnitControl(),
    )
    return spies


@pytest.mark.asyncio
async def test_creator_task_with_unretryable_rejection_is_finalized_rejected(
    monkeypatch,
) -> None:
    spies = await _run_creator_until_rejection(monkeypatch, reason_code=_UNRETRYABLE_REASON)

    spies.retry.assert_not_awaited()
    spies.failed.assert_awaited_once()
    result = spies.failed.await_args.kwargs["task_result"]
    assert result["outcome"] == "REJECTED"
    assert result.get("reconcile_required") is not True
    assert operator_reason_from_result(result) is not None


@pytest.mark.asyncio
async def test_creator_task_with_retryable_rejection_returns_to_queue(monkeypatch) -> None:
    spies = await _run_creator_until_rejection(monkeypatch, reason_code=_RETRYABLE_REASON)

    spies.retry.assert_awaited_once()
    spies.failed.assert_not_awaited()
