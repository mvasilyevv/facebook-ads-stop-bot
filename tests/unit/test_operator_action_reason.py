# -*- coding: utf-8 -*-
"""Причина отказа действия приходит оператору из события, а не из состояния (#206).

Инвариант: два действия в одном состоянии, упавшие по разным причинам, читаются
по-разному. Константа по состоянию давала пяти разным заливам один текст.
"""

from __future__ import annotations

from typing import Any

import apps.campaign_creator_worker.main as worker
from core.campaign_builder.execute import PartialCreateError
from core.meta_api.errors import (
    BrowserOperationRejectedError,
    PermanentError,
    classify_graph_error,
)
from core.operator.queries import task_action_reason, task_action_state
from core.tasks.action_reason import OPERATOR_REASON_MAX_LEN


class _Task:
    """Минимальная задача: результату нужны только correlation_id и id."""

    id = 42
    correlation_id = None


def _disabled_account_failure() -> PermanentError:
    """Отказ Meta «кабинет отключён» — тот же путь, что и у боевого залива."""
    exc = classify_graph_error(
        100,
        1885316,
        "Отключенные аккаунты не могут создавать или редактировать рекламу",
        endpoint="/act_1/adcreatives",
    )
    assert isinstance(exc, PermanentError)
    return exc


def _lost_page_failure() -> PartialCreateError:
    """Ответ Meta потерян вместе с контекстом страницы: ни одного id."""
    cause = classify_graph_error(-3, None, "page context destroyed")
    exc = PartialCreateError(
        "ack lost",
        created_ids={"campaigns": [], "adsets": [], "ads": []},
        failed_step="creating",
    )
    exc.__cause__ = cause
    return exc


def _browser_rejection_failure() -> BrowserOperationRejectedError:
    """Браузер отверг операцию до отправки: разрешение выдано другому кабинету."""
    return BrowserOperationRejectedError(
        "разрешение выдано на другой рекламный кабинет",
        reason_code="capability_cabinet_mismatch",
    )


def _reason_of(result: dict[str, Any]) -> str | None:
    return task_action_reason(result)


def test_two_failed_uploads_of_different_causes_read_differently() -> None:
    deadline = worker._campaign_rejected_result(  # noqa: SLF001
        run_id="00000000-0000-4000-8000-000000000001",
        reason="absolute_deadline_exceeded_before_external_call",
        failed_step="uploading",
    )
    disabled_account = worker._campaign_rejected_result(  # noqa: SLF001
        run_id="00000000-0000-4000-8000-000000000002",
        reason="permanent_pre_external_failure",
        failed_step="creating",
        exc=_disabled_account_failure(),
    )
    browser_rejected = worker._campaign_unknown_result(  # noqa: SLF001
        _Task(),
        run_id="00000000-0000-4000-8000-000000000003",
        reason="ack_lost_nothing_confirmed",
        failed_step="creating",
        pre_dispatch_reason_code="capability_cabinet_mismatch",
        exc=_browser_rejection_failure(),
    )
    lost_page = worker._campaign_unknown_result(  # noqa: SLF001
        _Task(),
        run_id="00000000-0000-4000-8000-000000000004",
        reason="external_result_ambiguous",
        failed_step="creating",
        exc=_lost_page_failure(),
    )

    texts = [
        _reason_of(deadline),
        _reason_of(disabled_account),
        _reason_of(browser_rejected),
        _reason_of(lost_page),
    ]

    assert all(text for text in texts)
    assert len(set(texts)) == len(texts)


def test_failed_upload_names_the_step_and_the_meta_answer() -> None:
    result = worker._campaign_rejected_result(  # noqa: SLF001
        run_id="00000000-0000-4000-8000-000000000005",
        reason="permanent_pre_external_failure",
        failed_step="creating",
        exc=_disabled_account_failure(),
    )

    reason = _reason_of(result)

    assert reason is not None
    assert "создание объектов кампании" in reason
    assert "Отключенные аккаунты" in reason
    # Машинный код причины остаётся в результате для разбора, но не в тексте.
    assert result["reason"] == "permanent_pre_external_failure"
    assert "permanent_pre_external_failure" not in reason


def test_failed_upload_reason_differs_from_a_failed_pause_of_the_same_state() -> None:
    """Соседнее упавшее действие другого рода не читается тем же текстом."""
    upload = worker._campaign_unknown_result(  # noqa: SLF001
        _Task(),
        run_id="00000000-0000-4000-8000-000000000006",
        reason="ack_lost_nothing_confirmed",
        failed_step="creating",
        exc=_lost_page_failure(),
    )
    # Отключение рекламы финализируется своим воркером и операторской причины
    # пока не записывает: у него честное «неизвестна», а не текст залива.
    pause = {"outcome": "UNKNOWN", "reason": "ambiguous_result"}

    assert task_action_state("failed", upload) == task_action_state("failed", pause)
    assert _reason_of(upload) is not None
    assert _reason_of(pause) is None


def test_unknown_reason_code_is_not_echoed_to_the_operator() -> None:
    """Незнакомый машинный код не превращается в «причину» сам по себе."""
    result = worker._campaign_rejected_result(  # noqa: SLF001
        run_id="00000000-0000-4000-8000-000000000007",
        reason="some_future_internal_code",
        failed_step="creating",
    )

    assert "operator_reason" not in result
    assert _reason_of(result) is None


def test_operator_reason_stays_within_one_ledger_line() -> None:
    long_message = classify_graph_error(100, None, "отказ " * 200)
    result = worker._campaign_rejected_result(  # noqa: SLF001
        run_id="00000000-0000-4000-8000-000000000008",
        reason="permanent_pre_external_failure",
        failed_step="creating",
        exc=long_message,
    )

    reason = _reason_of(result)

    assert reason is not None
    assert len(reason) <= OPERATOR_REASON_MAX_LEN
