# -*- coding: utf-8 -*-
"""Тесты recommendation worker для включения."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _rows_result(rows):
    """Создаёт мок результата scalars().unique().all() и scalars().all()."""
    result = MagicMock()
    scalars_mock = result.scalars.return_value
    scalars_mock.all.return_value = rows
    scalars_mock.unique.return_value.all.return_value = rows
    return result


def _make_session():
    """Создаёт мок async-сессии SQLAlchemy."""
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.commit = AsyncMock()
    return session


# Проверяем, что один цикл воркера публикует рекомендации и сохраняет первую Telegram-ссылку.
@pytest.mark.asyncio
async def test_process_enable_recommendation_cycle_broadcasts_and_attaches_refs():
    from apps.enable_recommendation_worker.main import process_enable_recommendation_cycle

    session_create = _make_session()
    session_pending = _make_session()
    session_attach = _make_session()
    factory = MagicMock(side_effect=[session_create, session_pending, session_attach])
    event = SimpleNamespace(
        id=uuid.uuid4(),
        ad_id=uuid.uuid4(),
        delivery_status="OFF",
        recommendation_level=SimpleNamespace(value="OK"),
        matched_rule_codes=[],
        reason_title="Норма",
        reason_text="Можно вернуть в работу.",
        metrics_json={"spend": "10.00"},
        fb_ad=SimpleNamespace(
            fb_ad_id="ad-1",
            ad_name="Reco Ad",
            adset=SimpleNamespace(
                adset_name="Adset 1",
                campaign=SimpleNamespace(
                    campaign_name="Campaign 1",
                ),
            ),
        ),
    )

    with (
        patch(
            "apps.enable_recommendation_worker.main.get_session_factory",
            return_value=factory,
        ),
        patch(
            "apps.enable_recommendation_worker.main.collect_enable_recommendation_candidates",
            new=AsyncMock(return_value=(datetime(2026, 3, 29, 13, 0, tzinfo=UTC), ["candidate"])),
        ),
        patch(
            "apps.enable_recommendation_worker.main.persist_enable_recommendation_candidates",
            new=AsyncMock(return_value=[event]),
        ),
        patch(
            "apps.enable_recommendation_worker.main.load_pending_enable_recommendation_events",
            new=AsyncMock(return_value=[event]),
        ),
        patch(
            "apps.enable_recommendation_worker.main.broadcast_enable_recommendation_message",
            new=AsyncMock(return_value=[("100", 55)]),
        ) as broadcast_mock,
        patch(
            "apps.enable_recommendation_worker.main.attach_recommendation_telegram_delivery",
            new=AsyncMock(),
        ) as attach_mock,
        patch(
            "apps.enable_recommendation_worker.main.get_observer_settings",
            new=AsyncMock(return_value=None),
        ),
    ):
        created = await process_enable_recommendation_cycle()

    assert created == 1
    broadcast_mock.assert_awaited_once()
    assert broadcast_mock.await_args.kwargs["campaign_name"] == "Campaign 1"
    assert broadcast_mock.await_args.kwargs["adset_name"] == "Adset 1"
    attach_mock.assert_awaited_once_with(
        session_attach,
        event_id=event.id,
        chat_id="100",
        message_id=55,
    )
    session_create.commit.assert_awaited_once()
    session_attach.commit.assert_awaited_once()


# Проверяем, что воркер досылает рекомендации текущего батча, если они уже созданы, но ещё не отправлены в Telegram.
@pytest.mark.asyncio
async def test_process_enable_recommendation_cycle_retries_pending_undelivered_events():
    from apps.enable_recommendation_worker.main import process_enable_recommendation_cycle

    session_create = _make_session()
    session_pending = _make_session()
    session_attach = _make_session()
    factory = MagicMock(side_effect=[session_create, session_pending, session_attach])
    event = SimpleNamespace(
        id=uuid.uuid4(),
        ad_id=uuid.uuid4(),
        delivery_status="OFF",
        recommendation_level=SimpleNamespace(value="OK"),
        matched_rule_codes=["early_lpv_ratio_signal"],
        reason_title="Мало переходов на PWA",
        reason_text="Есть несохранённая отправка Telegram.",
        metrics_json={"spend": "1.23"},
        fb_ad=SimpleNamespace(
            fb_ad_id="ad-pending",
            ad_name="Pending Ad",
            adset=SimpleNamespace(
                adset_name="Adset Pending",
                campaign=SimpleNamespace(
                    campaign_name="Campaign Pending",
                ),
            ),
        ),
    )

    with (
        patch(
            "apps.enable_recommendation_worker.main.get_session_factory",
            return_value=factory,
        ),
        patch(
            "apps.enable_recommendation_worker.main.collect_enable_recommendation_candidates",
            new=AsyncMock(return_value=(datetime(2026, 3, 29, 13, 0, tzinfo=UTC), ["candidate"])),
        ),
        patch(
            "apps.enable_recommendation_worker.main.persist_enable_recommendation_candidates",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "apps.enable_recommendation_worker.main.load_pending_enable_recommendation_events",
            new=AsyncMock(return_value=[event]),
        ),
        patch(
            "apps.enable_recommendation_worker.main.broadcast_enable_recommendation_message",
            new=AsyncMock(return_value=[("200", 77)]),
        ) as broadcast_mock,
        patch(
            "apps.enable_recommendation_worker.main.attach_recommendation_telegram_delivery",
            new=AsyncMock(),
        ) as attach_mock,
        patch(
            "apps.enable_recommendation_worker.main.get_observer_settings",
            new=AsyncMock(return_value=None),
        ),
    ):
        delivered = await process_enable_recommendation_cycle()

    assert delivered == 1
    assert broadcast_mock.await_args.kwargs["fb_ad_id"] == "ad-pending"
    attach_mock.assert_awaited_once_with(
        session_attach,
        event_id=event.id,
        chat_id="200",
        message_id=77,
    )
    session_create.commit.assert_awaited_once()
    session_attach.commit.assert_awaited_once()


# Проверяем, что recommendation worker корректно завершается по shutdown_event в idle-режиме.
@pytest.mark.asyncio
async def test_recommendation_worker_loop_stops_on_shutdown():
    from apps.enable_recommendation_worker.main import recommendation_worker_loop

    shutdown_event = asyncio.Event()
    call_count = 0

    async def cycle():
        nonlocal call_count
        call_count += 1
        shutdown_event.set()
        return 0

    await recommendation_worker_loop(
        poll_interval_seconds=0.01,
        shutdown_event=shutdown_event,
        process_cycle=cycle,
    )

    assert call_count == 1


# Проверяем, что ошибка внутри цикла не убивает воркер и он успевает сделать следующий проход.
@pytest.mark.asyncio
async def test_recommendation_worker_loop_recovers_after_error():
    from apps.enable_recommendation_worker.main import recommendation_worker_loop

    shutdown_event = asyncio.Event()
    call_count = 0

    async def cycle():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("boom")
        shutdown_event.set()
        return 0

    with patch(
        "apps.enable_recommendation_worker.main.process_enable_recommendation_cycle",
        new=AsyncMock(side_effect=cycle),
    ):
        await recommendation_worker_loop(
            poll_interval_seconds=0.01,
            shutdown_event=shutdown_event,
        )

    assert call_count >= 2
