# -*- coding: utf-8 -*-
"""Рабочий цикл доставки в Telegram закреплён поведением, а не сборкой (#251).

``run_worker`` не вызывался ни одним pytest-тестом: каждый его шаг покрыт в
изоляции, но порядок шагов и гейт «бот не аутентифицирован» держались только
на репетиционном скрипте. Любую из этих строк можно было удалить, и весь
набор оставался зелёным.

Здесь бот намеренно не настроен — это штатное, уже видимое оператору
состояние. Проверяется, что в нём воркер (1) всё равно числится живым,
(2) всё равно разгребает истёкшие lease и (3) НЕ трогает очередь доставок.
"""

from __future__ import annotations

import asyncio
import contextlib

import pytest

import apps.telegram_delivery_worker.main as delivery


class _Harness:
    """Один проход цикла с ненастроенным ботом; шпионы вместо БД и Bot API."""

    def __init__(self) -> None:
        self.pass_done = asyncio.Event()
        self.poll_marks: list[bool] = []
        self.delivery_reconciles = 0
        self.reply_reconciles = 0
        self.deliveries_claimed = 0
        self.replies_claimed = 0


@pytest.fixture
def unconfigured_bot(monkeypatch: pytest.MonkeyPatch) -> _Harness:
    harness = _Harness()

    async def _heartbeat(_engine, _name, *, poll_success=False):
        harness.poll_marks.append(bool(poll_success))

    async def _webhook_pass(_engine, *, worker_id):  # noqa: ARG001
        return False

    async def _no_config(_engine):
        # Токен бота не задан — gateway не создаётся, аутентификации нет.
        return None

    async def _reconcile_deliveries(_engine):
        harness.delivery_reconciles += 1
        return (0, 0)

    async def _reconcile_replies(_engine):
        harness.reply_reconciles += 1
        return (0, 0)

    async def _refresh_metrics(_engine):
        harness.pass_done.set()

    async def _claim_delivery(_engine, **_kwargs):
        harness.deliveries_claimed += 1
        return False

    async def _claim_reply(_engine, **_kwargs):
        harness.replies_claimed += 1
        return False

    monkeypatch.setattr(delivery, "start_worker_metrics_server", lambda *_a, **_k: None)
    monkeypatch.setattr(delivery, "record_worker_heartbeat", _heartbeat)
    monkeypatch.setattr(delivery, "process_one_webhook_configuration", _webhook_pass)
    monkeypatch.setattr(delivery, "load_telegram_config", _no_config)
    monkeypatch.setattr(delivery, "reconcile_expired_delivery_leases", _reconcile_deliveries)
    monkeypatch.setattr(delivery, "reconcile_expired_command_reply_leases", _reconcile_replies)
    monkeypatch.setattr(delivery, "refresh_notification_metrics", _refresh_metrics)
    monkeypatch.setattr(delivery, "process_one_delivery", _claim_delivery)
    monkeypatch.setattr(delivery, "process_one_command_reply", _claim_reply)
    return harness


async def _run_one_pass(harness: _Harness, *, grace: float = 0.25) -> None:
    """Гоняет цикл до конца прохода и даёт ему шанс пойти дальше положенного.

    ``grace`` существует ради проверки гейта: цикл без гейта дошёл бы до
    очереди доставок, и его нужно поймать, а не разминуться с ним отменой.
    """
    worker = asyncio.create_task(delivery.run_worker(engine=object()))
    try:
        await asyncio.wait_for(harness.pass_done.wait(), timeout=10.0)
        await asyncio.sleep(grace)
    finally:
        worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker


@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_worker_with_no_bot_token_still_reports_a_successful_poll(
    unconfigured_bot: _Harness,
) -> None:
    """Ненастроенный бот — отдельное состояние, а не повод считать воркер зависшим."""
    await _run_one_pass(unconfigured_bot)

    assert unconfigured_bot.poll_marks == [True]


@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_expired_leases_are_reconciled_even_while_the_bot_stays_unusable(
    unconfigured_bot: _Harness,
) -> None:
    """Брошенные доставки возвращаются в оборот независимо от исправности бота."""
    await _run_one_pass(unconfigured_bot)

    assert unconfigured_bot.delivery_reconciles == 1
    assert unconfigured_bot.reply_reconciles == 1


@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_unauthenticated_worker_never_claims_a_delivery(
    unconfigured_bot: _Harness,
) -> None:
    """Без рабочего канала строка доставки не захватывается.

    Захват сжёг бы попытку и lease на заведомо невозможной отправке, а инцидент
    получил бы отказ вместо честного «канал не настроен».

    Ожидание длиннее собственной паузы гейта (2 с): цикл, у которого гейт
    убрали, дошёл бы до очереди именно после неё, и более короткая отмена
    просто разминулась бы с ним, оставив тест ложно зелёным.
    """
    await _run_one_pass(unconfigured_bot, grace=3.0)

    assert unconfigured_bot.deliveries_claimed == 0
    assert unconfigured_bot.replies_claimed == 0
