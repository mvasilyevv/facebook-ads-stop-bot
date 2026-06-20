# -*- coding: utf-8 -*-
"""Task 4 Волна 2: worker-нотификации через recipients-путь (без forum-топиков).

Проверяем, что каждый воркер использует recipients-канал и НЕ использует forum_thread.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# =================== reconciler ===================


# reconciler: _maybe_alert_irreversible зовёт notify_recipients (не прямой send с thread_id)
# Функция делает lazy import из worker_notify → патчим там
@pytest.mark.asyncio
async def test_reconciler_alert_calls_notify_recipients() -> None:
    import core.telegram.worker_notify as wnm
    from apps.reconciler_worker.worker import _maybe_alert_irreversible

    engine = MagicMock()
    spy = AsyncMock(return_value=True)
    orig = wnm.notify_recipients
    wnm.notify_recipients = spy  # type: ignore[assignment]
    try:
        await _maybe_alert_irreversible(engine, count=2)
    finally:
        wnm.notify_recipients = orig

    spy.assert_awaited_once()
    kwargs = spy.await_args.kwargs
    assert kwargs["category"] == "reconciler_irreversible"
    assert "необратимых" in kwargs["text"].lower() or "мутаций" in kwargs["text"].lower()


# reconciler: при count <= 0 notify_recipients НЕ вызывается
@pytest.mark.asyncio
async def test_reconciler_alert_zero_skips() -> None:
    import core.telegram.worker_notify as wnm
    from apps.reconciler_worker.worker import _maybe_alert_irreversible

    engine = MagicMock()
    spy = AsyncMock(return_value=True)
    orig = wnm.notify_recipients
    wnm.notify_recipients = spy  # type: ignore[assignment]
    try:
        await _maybe_alert_irreversible(engine, count=0)
    finally:
        wnm.notify_recipients = orig
    spy.assert_not_awaited()


# =================== health_watchdog ===================


# health: _maybe_alert_with_dedup зовёт notify_recipients и ставит дедуп при успехе
@pytest.mark.asyncio
async def test_health_with_engine_calls_notify_recipients(monkeypatch) -> None:
    import apps.health_watchdog.main as hw

    engine = MagicMock()
    spy_notify = AsyncMock(return_value=True)
    monkeypatch.setattr(hw, "notify_recipients", spy_notify)

    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock(return_value=True)

    ok = await hw._maybe_alert_with_dedup(
        redis,
        dedup_key="test:key",
        text="алерт",
        engine=engine,
    )
    assert ok is True
    spy_notify.assert_awaited_once()


# =================== autostop_alert ===================


# channel-down: вызывает notify_recipients (lazy import из worker_notify), НЕ send напрямую
@pytest.mark.asyncio
async def test_autostop_alert_engine_path_uses_notify_recipients() -> None:
    import core.telegram.worker_notify as wnm
    from core.meta_api.autostop_alert import maybe_alert_autostop_channel_down
    from core.meta_api.errors import TemporaryError

    engine = MagicMock()
    redis = AsyncMock()

    exc = TemporaryError("Failed to fetch", code=-2)

    spy_notify = AsyncMock(return_value=True)
    orig = wnm.notify_recipients
    wnm.notify_recipients = spy_notify  # type: ignore[assignment]
    try:
        with patch(
            "core.meta_api.autostop_alert.register_autostop_failure_and_should_alert",
            AsyncMock(return_value=True),
        ):
            result = await maybe_alert_autostop_channel_down(
                redis,
                exc=exc,
                fb_ad_id="AD_123",
                engine=engine,
            )
    finally:
        wnm.notify_recipients = orig

    assert result is True
    spy_notify.assert_awaited_once()


# =================== enable_reco ===================


def _fake_candidate() -> object:
    """Минимальный CandidateRow для тестов send_alert."""
    import datetime as dt

    from apps.enable_recommendation_worker.main import CandidateRow

    return CandidateRow(
        ad_id=uuid.uuid4(),
        fb_ad_id="ACT_123_456",
        ad_name="Test Ad",
        campaign_name="Test Campaign",
        adset_name="Test AdSet",
        alert_state="disabled",
        last_transition_at=dt.datetime(2026, 6, 1, 0, 0, tzinfo=dt.timezone.utc),
        snoozed_until=None,
        offer_code="CR2",
        cpa_threshold=None,
    )


def _fake_decision():
    from core.enable_reco.analyzer import RecommendationDecision

    return RecommendationDecision(recommend=True, level="warning", skip_reason=None, snapshot={})


# enable_reco: send_alert рассылает по recipients, НЕ через forum-thread
@pytest.mark.asyncio
async def test_enable_reco_send_alert_engine_sends_to_recipients() -> None:
    from apps.enable_recommendation_worker.main import send_alert

    engine = MagicMock()
    tg_client = AsyncMock()
    tg_client.send_message = AsyncMock(return_value={"ok": True})

    fake_recipient = SimpleNamespace(chat_id=111)
    with (
        patch(
            "apps.enable_recommendation_worker.main.load_active_recipients",
            AsyncMock(return_value=[fake_recipient]),
        ),
        patch(
            "apps.enable_recommendation_worker.main.load_web_app_url",
            AsyncMock(return_value=None),
        ),
    ):
        result = await send_alert(
            tg_client,
            candidate=_fake_candidate(),
            decision=_fake_decision(),
            engine=engine,
        )

    assert result is True
    tg_client.send_message.assert_awaited_once()
    kwargs = tg_client.send_message.await_args.kwargs
    assert kwargs["chat_id"] == "111"
    # message_thread_id не передаётся
    assert "message_thread_id" not in kwargs


# enable_reco: _default_tg_factory не передаёт thread_id (форум-топики удалены в Волне 2)
@pytest.mark.asyncio
async def test_enable_reco_default_tg_factory_no_thread() -> None:
    fake_cfg = SimpleNamespace(bot_token="tok", chat_id=99)
    engine = MagicMock()
    with patch(
        "apps.enable_recommendation_worker.main.load_telegram_config",
        AsyncMock(return_value=fake_cfg),
    ):
        from apps.enable_recommendation_worker.main import _default_tg_factory

        _, _, thread = await _default_tg_factory(engine)
    assert thread is None


# =================== digest ===================


# digest: run_one_tick НЕ отправляет в forum_digest_thread (форум-топики удалены в Волне 2)
@pytest.mark.asyncio
async def test_digest_no_forum_thread_send() -> None:
    from datetime import datetime, timezone

    import apps.digest_scheduler.main as dg

    now = datetime(2026, 6, 20, 9, 30, tzinfo=timezone.utc)
    window = dg.DigestWindow(hour=9, minute=0, window_minutes=5)

    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock(return_value=True)

    fake_cfg = SimpleNamespace(
        bot_token="tok",
        chat_id=99,
    )
    tg_client = AsyncMock()
    tg_client.send_message = AsyncMock(return_value={"ok": True})
    tg_client.close = AsyncMock()

    from core.telegram.service import Recipient as _Recipient

    # Используем core-объект Recipient (консолидация Task 4 Волна 4)
    fake_recipients = [_Recipient(chat_id=111, telegram_user_id=999, username=None, role="owner")]
    engine = MagicMock()

    with (
        patch("apps.digest_scheduler.main.load_telegram_config", AsyncMock(return_value=fake_cfg)),
        patch(
            "apps.digest_scheduler.main.load_active_recipients",
            AsyncMock(return_value=fake_recipients),
        ),
        patch("apps.digest_scheduler.main.build_digest", AsyncMock(return_value={})),
        patch("apps.digest_scheduler.main.render_digest", return_value="<b>Digest</b>"),
    ):
        status = await dg.run_one_tick(
            engine=engine,
            redis_client=redis,
            tg_client_factory=lambda token: tg_client,
            now=now,
            window=window,
        )

    assert status == "sent"
    # Только одна отправка — в личку recipient'а (не в forum-топик)
    call_count = tg_client.send_message.await_count
    assert call_count == 1
    call_kwargs = tg_client.send_message.await_args_list[0].kwargs
    assert call_kwargs["chat_id"] == "111"
    # Нет вызова с chat_id=99 (forum-группа) и thread_id=33
    for call in tg_client.send_message.await_args_list:
        assert call.kwargs.get("chat_id") != "99"
        assert call.kwargs.get("message_thread_id") != 33
