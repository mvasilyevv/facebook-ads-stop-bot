# -*- coding: utf-8 -*-
"""Unit-тесты для apps/api/utils/status_mapper.py.

Проверяем маппинг TaskQueue.status (lowercase из v2-БД) ↔ frontend (UPPERCASE).
"""

from __future__ import annotations

import pytest

from apps.api.utils.status_mapper import from_frontend_task_status, to_frontend_task_status


# draft считается черновиком и маппится в PENDING — фронт не знает о draft-состоянии.
def test_draft_maps_to_pending() -> None:
    assert to_frontend_task_status("draft") == "PENDING"


# pending → PENDING: обычная очередь до обработки воркером.
def test_pending_maps_to_pending() -> None:
    assert to_frontend_task_status("pending") == "PENDING"


# running → RUNNING: воркер захватил задачу и выполняет.
def test_running_maps_to_running() -> None:
    assert to_frontend_task_status("running") == "RUNNING"


# succeeded → SUCCEEDED: задача успешно выполнена.
def test_succeeded_maps_to_succeeded() -> None:
    assert to_frontend_task_status("succeeded") == "SUCCEEDED"


# failed → FAILED: превышен лимит попыток, задача окончательно провалена.
def test_failed_maps_to_failed() -> None:
    assert to_frontend_task_status("failed") == "FAILED"


# retrying → RETRYING: временная ошибка, воркер планирует повтор с backoff.
def test_retrying_maps_to_retrying() -> None:
    assert to_frontend_task_status("retrying") == "RETRYING"


# cancelled → CANCELLED: пользователь или reconciler отменил задачу.
def test_cancelled_maps_to_cancelled() -> None:
    assert to_frontend_task_status("cancelled") == "CANCELLED"


# Неизвестный db-статус поднимает ValueError с понятным сообщением.
def test_unknown_db_status_raises_value_error() -> None:
    with pytest.raises(ValueError, match="Неизвестный db-статус"):
        to_frontend_task_status("unknown_garbage")


# Обратный маппинг PENDING → pending (не draft — draft внутренний).
def test_pending_frontend_maps_to_pending_db() -> None:
    assert from_frontend_task_status("PENDING") == "pending"


# Обратный маппинг RUNNING → running.
def test_running_frontend_maps_to_running_db() -> None:
    assert from_frontend_task_status("RUNNING") == "running"


# Обратный маппинг SUCCEEDED → succeeded.
def test_succeeded_frontend_maps_to_succeeded_db() -> None:
    assert from_frontend_task_status("SUCCEEDED") == "succeeded"


# Обратный маппинг FAILED → failed.
def test_failed_frontend_maps_to_failed_db() -> None:
    assert from_frontend_task_status("FAILED") == "failed"


# Обратный маппинг RETRYING → retrying.
def test_retrying_frontend_maps_to_retrying_db() -> None:
    assert from_frontend_task_status("RETRYING") == "retrying"


# Обратный маппинг CANCELLED → cancelled.
def test_cancelled_frontend_maps_to_cancelled_db() -> None:
    assert from_frontend_task_status("CANCELLED") == "cancelled"


# Неизвестный frontend-статус поднимает ValueError.
def test_unknown_frontend_status_raises_value_error() -> None:
    with pytest.raises(ValueError, match="Неизвестный frontend-статус"):
        from_frontend_task_status("INVALID_STATUS")
