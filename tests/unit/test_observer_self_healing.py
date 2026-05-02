# -*- coding: utf-8 -*-
"""Unit-тесты SelfHealingEscalator — self-healing эскалатор observer worker."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.observer.self_healing import _CRITICAL_ALERT_COOLDOWN_SECONDS, SelfHealingEscalator


# Все тесты этого модуля автоматически замокивают asyncio.sleep внутри self_healing,
# чтобы backoff между провалами не тормозил тестовый прогон.
@pytest.fixture(autouse=True)
def _mock_sleep():
    with patch("core.observer.self_healing.asyncio.sleep", new=AsyncMock()):
        yield


# ---------------------------------------------------------------------------
# Вспомогательные фикстуры
# ---------------------------------------------------------------------------


def _make_grpc_client():
    """Создаёт мок BrowserAgentClient с нужными async-методами."""
    client = MagicMock()
    client.reconnect_browser = AsyncMock(return_value="session-1")
    client.stop_browser = AsyncMock()
    client.start_browser = AsyncMock(return_value="session-1")
    return client


def _make_tg_client():
    """Создаёт мок TelegramBotClient."""
    client = MagicMock()
    client.send_message = AsyncMock(return_value={"message_id": 42})
    return client


# ---------------------------------------------------------------------------
# Сценарий 1: счётчик инкрементируется при ошибках и сбрасывается при успехе
# ---------------------------------------------------------------------------


# Сценарий 1a: при первом провале счётчик становится 1, reconnect не вызывается
@pytest.mark.asyncio
async def test_failure_count_increments_on_first_failure():
    escalator = SelfHealingEscalator()
    grpc_client = _make_grpc_client()
    tg_client = _make_tg_client()

    await escalator.record_failure(
        grpc_client=grpc_client, tg_client=tg_client, tg_chat_id="chat123"
    )

    assert escalator.consecutive_failure_count == 1
    grpc_client.reconnect_browser.assert_not_called()
    grpc_client.stop_browser.assert_not_called()
    grpc_client.start_browser.assert_not_called()
    tg_client.send_message.assert_not_called()


# Сценарий 1b: счётчик сбрасывается при успешном цикле
@pytest.mark.asyncio
async def test_failure_count_resets_after_success():
    escalator = SelfHealingEscalator()
    grpc_client = _make_grpc_client()
    tg_client = _make_tg_client()

    # Два провала
    await escalator.record_failure(
        grpc_client=grpc_client, tg_client=tg_client, tg_chat_id="chat123"
    )
    await escalator.record_failure(
        grpc_client=grpc_client, tg_client=tg_client, tg_chat_id="chat123"
    )
    assert escalator.consecutive_failure_count == 2

    # Успешный цикл
    escalator.record_success()
    assert escalator.consecutive_failure_count == 0


# ---------------------------------------------------------------------------
# Сценарий 2: при count=2 вызывается reconnect; при count=3 — stop + start
# ---------------------------------------------------------------------------


# Сценарий 2a: второй провал вызывает reconnect_browser
@pytest.mark.asyncio
async def test_second_failure_calls_reconnect():
    escalator = SelfHealingEscalator()
    grpc_client = _make_grpc_client()
    tg_client = _make_tg_client()

    # Первый провал
    await escalator.record_failure(
        grpc_client=grpc_client, tg_client=tg_client, tg_chat_id="chat123"
    )
    grpc_client.reconnect_browser.assert_not_called()

    # Второй провал
    await escalator.record_failure(
        grpc_client=grpc_client, tg_client=tg_client, tg_chat_id="chat123"
    )
    grpc_client.reconnect_browser.assert_called_once()
    grpc_client.stop_browser.assert_not_called()


# Сценарий 2b: третий провал вызывает stop_browser + start_browser
@pytest.mark.asyncio
async def test_third_failure_calls_stop_then_start():
    escalator = SelfHealingEscalator()
    grpc_client = _make_grpc_client()
    tg_client = _make_tg_client()

    for _ in range(3):
        await escalator.record_failure(
            grpc_client=grpc_client, tg_client=tg_client, tg_chat_id="chat123"
        )

    grpc_client.stop_browser.assert_called_once()
    grpc_client.start_browser.assert_called_once()
    # reconnect — только на count=2
    grpc_client.reconnect_browser.assert_called_once()


# ---------------------------------------------------------------------------
# Сценарий 3: крит-алерт отправляется ровно один раз при count=4;
#             повторный count=5 не шлёт ещё одно в течение cooldown
# ---------------------------------------------------------------------------


# Сценарий 3a: при переходе на count=4 крит-алерт отправляется ровно один раз
@pytest.mark.asyncio
async def test_critical_alert_sent_once_on_count_4():
    escalator = SelfHealingEscalator()
    grpc_client = _make_grpc_client()
    tg_client = _make_tg_client()

    for _ in range(4):
        await escalator.record_failure(
            grpc_client=grpc_client, tg_client=tg_client, tg_chat_id="chat123"
        )

    assert tg_client.send_message.call_count == 1
    msg_text = tg_client.send_message.call_args[1]["text"]
    assert "🚨" in msg_text
    assert "4" in msg_text or "Observer" in msg_text
    assert escalator._last_critical_alert_at is not None


# Сценарий 3b: count=5 в рамках cooldown не шлёт повторный алерт
@pytest.mark.asyncio
async def test_critical_alert_not_repeated_within_cooldown():
    escalator = SelfHealingEscalator()
    grpc_client = _make_grpc_client()
    tg_client = _make_tg_client()

    for _ in range(5):
        await escalator.record_failure(
            grpc_client=grpc_client, tg_client=tg_client, tg_chat_id="chat123"
        )

    # Всего один алерт (при count=4), count=5 внутри cooldown — молчание
    assert tg_client.send_message.call_count == 1


# Сценарий 3c: алерт повторяется после истечения cooldown
@pytest.mark.asyncio
async def test_critical_alert_repeats_after_cooldown():
    escalator = SelfHealingEscalator()
    grpc_client = _make_grpc_client()
    tg_client = _make_tg_client()

    # Доводим до count=4 — отправится первый алерт
    for _ in range(4):
        await escalator.record_failure(
            grpc_client=grpc_client, tg_client=tg_client, tg_chat_id="chat123"
        )
    assert tg_client.send_message.call_count == 1

    # Переводим временну́ю метку в прошлое за пределы cooldown
    escalator._last_critical_alert_at = datetime.now(UTC) - timedelta(
        seconds=_CRITICAL_ALERT_COOLDOWN_SECONDS + 10
    )

    # Ещё один провал — должен отправить второй алерт
    await escalator.record_failure(
        grpc_client=grpc_client, tg_client=tg_client, tg_chat_id="chat123"
    )
    assert tg_client.send_message.call_count == 2


# ---------------------------------------------------------------------------
# Сценарий 4: unknown_modal_artifacts вызывают TG-алерт, но не инкрементят counter
# ---------------------------------------------------------------------------


# Сценарий 4a: неизвестная модалка отправляет алерт, счётчик не трогает
@pytest.mark.asyncio
async def test_unknown_modal_sends_alert_without_incrementing_counter():
    escalator = SelfHealingEscalator()
    tg_client = _make_tg_client()

    artifacts = ["/am/modal/unknown_overlay", "/am/modal/gdpr_notice"]

    await escalator.handle_unknown_modal_artifacts(
        artifacts, tg_client=tg_client, tg_chat_id="chat123"
    )

    assert escalator.consecutive_failure_count == 0
    tg_client.send_message.assert_called_once()
    msg_text = tg_client.send_message.call_args[1]["text"]
    # Текст должен говорить о неумении закрыть окно и содержать пути артефактов
    assert "окно" in msg_text.lower() or "модалка" in msg_text.lower()
    assert "/am/modal/unknown_overlay" in msg_text


# Сценарий 4b: пустой список артефактов — алерт не отправляется
@pytest.mark.asyncio
async def test_empty_modal_artifacts_sends_no_alert():
    escalator = SelfHealingEscalator()
    tg_client = _make_tg_client()

    await escalator.handle_unknown_modal_artifacts([], tg_client=tg_client, tg_chat_id="chat123")

    tg_client.send_message.assert_not_called()
    assert escalator.consecutive_failure_count == 0


# ---------------------------------------------------------------------------
# Сценарий 5: heartbeat записывается в БД при вызове update_observer_runtime_status
# ---------------------------------------------------------------------------


# Сценарий 5: update_observer_runtime_status вызывается с актуальным timestamp
@pytest.mark.asyncio
async def test_heartbeat_calls_update_observer_runtime_status():
    """Убеждаемся, что update_observer_runtime_status вызывается с текущим временем."""
    before = datetime.now(UTC)

    with patch("core.observer.runtime_status.get_session_factory") as mock_factory:
        # Настраиваем mock на контекстный менеджер
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        mock_factory.return_value = MagicMock(return_value=mock_session)

        from core.observer.runtime_status import update_observer_runtime_status

        # Мокируем get_or_create_observer_settings чтобы не ходить в реальную БД
        mock_row = MagicMock()
        with patch(
            "core.observer.runtime_status.get_or_create_observer_settings",
            new=AsyncMock(return_value=mock_row),
        ):
            await update_observer_runtime_status(
                status="RUNNING",
                message="Выполняется цикл сканирования.",
                clear_last_error=True,
            )

            # Проверяем, что heartbeat_at выставлен и он актуален
            heartbeat = mock_row.worker_heartbeat_at
            after = datetime.now(UTC)
            assert before <= heartbeat <= after, (
                f"heartbeat_at={heartbeat} должен быть между {before} и {after}"
            )


# ---------------------------------------------------------------------------
# Сценарий 6: крит-алерт уходит на ops-stream (message_thread_id корректный)
# ---------------------------------------------------------------------------


# Сценарий 6a: при count=4 send_message вызван с message_thread_id из ops-потока
@pytest.mark.asyncio
async def test_critical_alert_uses_ops_thread_id():
    """При forum_topics_enabled=True и topic_ops_thread_id=42 — send_message получает message_thread_id=42."""
    escalator = SelfHealingEscalator()
    grpc_client = _make_grpc_client()
    tg_client = _make_tg_client()

    fake_settings_row = MagicMock()
    fake_settings_row.forum_topics_enabled = True
    fake_settings_row.topic_ops_thread_id = 42

    fake_session = MagicMock()
    fake_session.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session.__aexit__ = AsyncMock(return_value=None)
    fake_session.scalar = AsyncMock(return_value=fake_settings_row)
    fake_factory = MagicMock(return_value=fake_session)

    with patch("core.observer.self_healing.get_session_factory", return_value=fake_factory):
        for _ in range(4):
            await escalator.record_failure(
                grpc_client=grpc_client, tg_client=tg_client, tg_chat_id="chat123"
            )

    assert tg_client.send_message.call_count == 1
    call_kwargs = tg_client.send_message.call_args[1]
    assert call_kwargs.get("message_thread_id") == 42
