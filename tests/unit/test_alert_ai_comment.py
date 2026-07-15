# -*- coding: utf-8 -*-
"""Unit-тесты AI-комментариев к алертам (Волна 2a) — проводка сирот explain/diagnose."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.telegram.alert_dispatcher import (
    _post_ai_comment,
    _spawn_ai_comment,
    _thresholds_from_hits,
)

_HITS_JSON = {
    "spend": "12.5",
    "cost_per_lead": "4.2",
    "_hits": [{"code": "cpl_stop", "stage": "stop", "value": "4.2", "threshold": "3.0"}],
}


# Порог сработавшего правила достаётся из _hits под ключом, понятным explain_alert
def test_thresholds_from_hits_maps_known_prefix() -> None:
    assert _thresholds_from_hits(_HITS_JSON, "cpl_stop") == {"cpl_stop_threshold": "3.0"}


# Правило без известного префикса — generic-ключ, а не потеря порога
def test_thresholds_from_hits_generic_key() -> None:
    mj = {"_hits": [{"code": "spend_no_dep_stop", "threshold": "7.0"}]}
    assert _thresholds_from_hits(mj, "spend_no_dep_stop") == {"stop_threshold": "7.0"}


# Успешное объяснение → 💡-реплай под алертом (reply_to_message_id = алерт)
@pytest.mark.asyncio
async def test_comment_sent_as_reply() -> None:
    client = MagicMock()
    client.send_message = AsyncMock()
    with patch(
        "core.telegram.alert_dispatcher.explain_alert",
        new=AsyncMock(return_value="CPL 4.2$ выше порога 3$ — лид дорогой."),
    ) as explain:
        await _post_ai_comment(
            client,
            chat_id=42,
            message_id=777,
            thread_id=None,
            stage="stop",
            matched_codes=["cpl_stop"],
            metrics_json=_HITS_JSON,
            offer_code="GH_CR2",
        )
    # _hits в метрики промпта не утекают, порог передан отдельно
    kwargs = explain.call_args.kwargs
    assert "_hits" not in kwargs["metrics"]
    assert kwargs["thresholds"] == {"cpl_stop_threshold": "3.0"}
    sent = client.send_message.call_args.kwargs
    assert sent["reply_to_message_id"] == 777
    assert sent["text"].startswith("💡")


# explain_alert вернул None (AI молчит) — сообщение не отправляется
@pytest.mark.asyncio
async def test_no_comment_when_explain_none() -> None:
    client = MagicMock()
    client.send_message = AsyncMock()
    with patch("core.telegram.alert_dispatcher.explain_alert", new=AsyncMock(return_value=None)):
        await _post_ai_comment(
            client,
            chat_id=42,
            message_id=777,
            thread_id=None,
            stage="stop",
            matched_codes=["cpl_stop"],
            metrics_json=_HITS_JSON,
            offer_code=None,
        )
    client.send_message.assert_not_called()


# Падение explain_alert НЕ пробрасывается наружу (money-путь не задевается)
@pytest.mark.asyncio
async def test_comment_crash_is_swallowed() -> None:
    client = MagicMock()
    client.send_message = AsyncMock()
    with patch(
        "core.telegram.alert_dispatcher.explain_alert",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        await _post_ai_comment(
            client,
            chat_id=42,
            message_id=777,
            thread_id=None,
            stage="stop",
            matched_codes=["cpl_stop"],
            metrics_json=_HITS_JSON,
            offer_code=None,
        )  # не бросает
    client.send_message.assert_not_called()


# Флаг ai_explain_alerts_enabled=False — фоновый таск вообще не создаётся
@pytest.mark.asyncio
async def test_spawn_respects_disabled_flag() -> None:
    settings = MagicMock()
    settings.ai_explain_alerts_enabled = False
    with (
        patch("core.telegram.alert_dispatcher.get_settings", return_value=settings),
        patch("core.telegram.alert_dispatcher._post_ai_comment") as post,
    ):
        _spawn_ai_comment(
            client=MagicMock(),
            chat_id=1,
            message_id=1,
            thread_id=None,
            stage="stop",
            matched_codes=["cpl_stop"],
            metrics_json={},
            offer_code=None,
        )
        await asyncio.sleep(0)
    post.assert_not_called()


# Флаг включён — таск создаётся и доходит до explain (алерт при этом уже отправлен)
@pytest.mark.asyncio
async def test_spawn_creates_background_task() -> None:
    done = asyncio.Event()

    async def fake_post(**kwargs) -> None:
        done.set()

    settings = MagicMock()
    settings.ai_explain_alerts_enabled = True
    with (
        patch("core.telegram.alert_dispatcher.get_settings", return_value=settings),
        patch("core.telegram.alert_dispatcher._post_ai_comment", new=fake_post),
    ):
        _spawn_ai_comment(
            client=MagicMock(),
            chat_id=1,
            message_id=1,
            thread_id=None,
            stage="stop",
            matched_codes=["cpl_stop"],
            metrics_json={},
            offer_code=None,
        )
        await asyncio.wait_for(done.wait(), timeout=1)


# Watchdog: диагноз получен → отдельное сообщение 🩺 через notify_recipients
@pytest.mark.asyncio
async def test_watchdog_diagnosis_sent() -> None:
    from apps.health_watchdog.main import _post_ai_diagnosis

    with (
        patch(
            "core.ai_assistant.diagnostics.diagnose_alert",
            new=AsyncMock(return_value="<b>Причина:</b> Redis недоступен"),
        ),
        patch("apps.health_watchdog.main.notify_recipients", new=AsyncMock()) as notify,
    ):
        await _post_ai_diagnosis(
            engine=MagicMock(),
            redis_client=MagicMock(),
            alert_key="health:alerted:observer",
            alert_text="CRITICAL: observer не дышит",
        )
    text = notify.call_args.kwargs["text"]
    assert text.startswith("🩺")
    assert "Причина" in text


# Watchdog: диагноз None (кулдаун/AI выключен) — ничего не шлём
@pytest.mark.asyncio
async def test_watchdog_diagnosis_none_silent() -> None:
    from apps.health_watchdog.main import _post_ai_diagnosis

    with (
        patch("core.ai_assistant.diagnostics.diagnose_alert", new=AsyncMock(return_value=None)),
        patch("apps.health_watchdog.main.notify_recipients", new=AsyncMock()) as notify,
    ):
        await _post_ai_diagnosis(
            engine=MagicMock(),
            redis_client=MagicMock(),
            alert_key="health:alerted:observer",
            alert_text="CRITICAL",
        )
    notify.assert_not_called()
