# -*- coding: utf-8 -*-
"""Unit-тесты улучшений observer worker: jitter, batch upsert, FSM из БД, reconnect."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.domain import AlertState

# --- Тесты jitter (задача 1.5) ---


# Проверяем что compute_jitter возвращает значение в диапазоне 50-150% от interval
def test_compute_jitter_range():
    """Задержка должна быть в пределах 50-150% от interval_seconds."""
    from apps.observer_worker.main import compute_jitter

    interval = 90
    results = [compute_jitter(interval, 45) for _ in range(500)]

    for val in results:
        # Диапазон: interval * (1 - 0.5) ... interval * (1 + 0.5) = 45 .. 135
        assert 45.0 <= val <= 135.0, f"Значение jitter {val} выходит за допустимый диапазон 45-135"


# Проверяем что jitter даёт разные значения (не константа)
def test_compute_jitter_is_random():
    """Jitter должен давать разные значения при множестве вызовов."""
    from apps.observer_worker.main import compute_jitter

    results = {round(compute_jitter(90, 45), 2) for _ in range(100)}
    # При 100 вызовах должно быть минимум 10 уникальных значений
    assert len(results) > 10, "Jitter даёт слишком мало уникальных значений"


# --- Тесты batch upsert (задача 2.1) ---


# Проверяем что batch_save_snapshots вызывает один execute вместо N
@pytest.mark.asyncio
async def test_batch_save_snapshots_single_query():
    """Batch upsert должен выполнять один запрос на все снэпшоты, а не N."""
    from apps.observer_worker.main import batch_save_snapshots

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    mock_factory = MagicMock(return_value=mock_session)

    snapshot_data = [
        {
            "fb_ad_id": f"ad_{i}",
            "campaign_name": "campaign",
            "adset_name": "adset",
            "ad_name": f"ad_{i}",
            "delivery_status": "ACTIVE",
            "offer_id": None,
            "resolved_offer_code": None,
            "spend": Decimal("10.00"),
            "clicks": 5,
            "cpc": Decimal("2.00"),
            "leads": 1,
            "cost_per_lead": Decimal("10.00"),
            "registrations": 0,
            "cost_per_registration": None,
            "deposits": 0,
            "alert_state": AlertState.NORMAL,
            "current_stage": None,
            "warning_rule_codes": [],
            "stop_rule_codes": [],
            "open_state_token": None,
            "last_observed_at": None,
        }
        for i in range(50)
    ]

    with patch(
        "apps.observer_worker.main.get_session_factory",
        return_value=mock_factory,
    ), patch(
        "apps.observer_worker.main._maybe_rollover_cabinet_day",
        new=AsyncMock(),
    ):
        await batch_save_snapshots(snapshot_data)

    # Должен быть ровно один вызов execute (один INSERT для всех 50 строк)
    assert mock_session.execute.call_count == 1, (
        f"Ожидался 1 вызов execute, получено {mock_session.execute.call_count}"
    )
    # И один commit
    assert mock_session.commit.call_count == 1


# Проверяем что пустой список не вызывает запросов
@pytest.mark.asyncio
async def test_batch_save_snapshots_empty_list():
    """При пустом списке не должно быть обращений к БД."""
    from apps.observer_worker.main import batch_save_snapshots

    mock_factory = MagicMock()

    with patch(
        "apps.observer_worker.main.get_session_factory",
        return_value=mock_factory,
    ):
        await batch_save_snapshots([])

    # Фабрика не должна вызываться при пустом списке
    mock_factory.assert_not_called()


# --- Тесты FSM загрузки из БД (задача 2.3) ---


# Проверяем что ad_states заполняется из БД при старте
@pytest.mark.asyncio
async def test_load_ad_states_from_db():
    """FSM-состояния должны загружаться из AdSnapshot при старте."""
    from apps.observer_worker.main import load_ad_states_from_db

    # Мокаем результат запроса к БД
    mock_rows = [
        ("ad_001", AlertState.WARNING_SENT, "token_abc"),
        ("ad_002", AlertState.STOP_SENT, "token_def"),
        ("ad_003", AlertState.NORMAL, None),
    ]

    mock_result = MagicMock()
    mock_result.all.return_value = mock_rows

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.execute = AsyncMock(return_value=mock_result)

    mock_factory = MagicMock(return_value=mock_session)

    with patch(
        "apps.observer_worker.main.get_session_factory",
        return_value=mock_factory,
    ):
        states = await load_ad_states_from_db()

    # Проверяем что все три состояния загружены
    assert len(states) == 3
    assert states["ad_001"] == (AlertState.WARNING_SENT, "token_abc")
    assert states["ad_002"] == (AlertState.STOP_SENT, "token_def")
    assert states["ad_003"] == (AlertState.NORMAL, None)


# Проверяем что пустая БД даёт пустой dict
@pytest.mark.asyncio
async def test_load_ad_states_empty_db():
    """При пустой БД должен вернуться пустой словарь состояний."""
    from apps.observer_worker.main import load_ad_states_from_db

    mock_result = MagicMock()
    mock_result.all.return_value = []

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.execute = AsyncMock(return_value=mock_result)

    mock_factory = MagicMock(return_value=mock_session)

    with patch(
        "apps.observer_worker.main.get_session_factory",
        return_value=mock_factory,
    ):
        states = await load_ad_states_from_db()

    assert states == {}


# Проверяем что реальный OFF сохраняет DISABLED для ранее выключавшихся объявлений
def test_resolve_off_alert_state_keeps_disabled_for_claimed_and_disabled():
    """При delivery=OFF состояния CLAIMED и DISABLED должны оставаться терминальным DISABLED."""
    from apps.observer_worker.main import resolve_off_alert_state

    assert resolve_off_alert_state(AlertState.CLAIMED) == AlertState.DISABLED
    assert resolve_off_alert_state(AlertState.DISABLED) == AlertState.DISABLED
    assert resolve_off_alert_state(AlertState.NORMAL) == AlertState.NORMAL


# Проверяем что активная очередь отключения ставит observer на паузу
@pytest.mark.asyncio
async def test_get_disable_queue_pause_reason_reports_active_queue():
    """Если есть PENDING и RETRYING задачи, observer должен видеть причину для паузы."""
    from apps.observer_worker.main import get_disable_queue_pause_reason
    from core.domain import DisableTaskStatus

    mock_result = MagicMock()
    mock_result.all.return_value = [
        (DisableTaskStatus.PENDING, None),
        (DisableTaskStatus.RETRYING, None),
    ]

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.execute = AsyncMock(return_value=mock_result)

    mock_factory = MagicMock(return_value=mock_session)

    with patch(
        "apps.observer_worker.main.get_session_factory",
        return_value=mock_factory,
    ):
        reason = await get_disable_queue_pause_reason()

    assert reason is not None
    assert "ожидают: 1" in reason
    assert "повтор: 1" in reason


# Проверяем что пустая очередь не мешает observer продолжать скан
@pytest.mark.asyncio
async def test_get_disable_queue_pause_reason_returns_none_for_empty_queue():
    """Когда активных disable-задач нет, observer не должен ставить скан на паузу."""
    from apps.observer_worker.main import get_disable_queue_pause_reason

    mock_result = MagicMock()
    mock_result.all.return_value = []

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.execute = AsyncMock(return_value=mock_result)

    mock_factory = MagicMock(return_value=mock_session)

    with patch(
        "apps.observer_worker.main.get_session_factory",
        return_value=mock_factory,
    ):
        reason = await get_disable_queue_pause_reason()

    assert reason is None


# Проверяем что внешние изменения из БД перетирают устаревшее in-memory состояние
@pytest.mark.asyncio
async def test_refresh_runtime_ad_states_uses_db_as_source_of_truth():
    """Если Telegram перевёл объявление в CLAIMED, observer должен взять это из БД до нового скана."""
    from apps.observer_worker.main import refresh_runtime_ad_states

    current_states = {"ad_001": (AlertState.WARNING_SENT, "old-token")}
    persisted_states = {"ad_001": (AlertState.CLAIMED, "old-token")}

    with patch(
        "apps.observer_worker.main.load_ad_states_from_db",
        new=AsyncMock(return_value=persisted_states),
    ):
        refreshed = await refresh_runtime_ad_states(current_states)

    assert refreshed == persisted_states


# Проверяем что обычный ненулевой скан не инициализирует границу суток кабинета
@pytest.mark.asyncio
async def test_maybe_rollover_cabinet_day_waits_for_zero_scan():
    """До первого полного zero-scan cabinet_day_started_at не должен выставляться."""
    from apps.observer_worker.main import _maybe_rollover_cabinet_day

    settings = MagicMock()
    settings.cabinet_day_started_at = None

    execute_result = MagicMock()
    execute_result.scalars.return_value.all.return_value = []

    session = AsyncMock()
    session.execute = AsyncMock(return_value=execute_result)

    snapshot_data = [
        {
            "fb_ad_id": "ad_1",
            "campaign_name": "Campaign A",
            "spend": Decimal("10.00"),
            "clicks": 5,
            "leads": 1,
            "registrations": 0,
            "deposits": 0,
        }
    ]

    with patch(
        "apps.observer_worker.main._get_or_create_observer_settings",
        new=AsyncMock(return_value=settings),
    ):
        await _maybe_rollover_cabinet_day(session, snapshot_data)

    assert settings.cabinet_day_started_at is None
    session.add.assert_not_called()


# --- Тесты reconnect (задача 2.4) ---


# Проверяем retry-логику при ошибке браузера
@pytest.mark.asyncio
async def test_reconnect_on_browser_error():
    """При ошибке связи с браузером должна быть попытка переподключения."""
    from apps.observer_worker.main import observer_loop

    mock_page = AsyncMock()
    mock_browser_manager = AsyncMock()
    mock_browser_manager.get_page.return_value = mock_page

    call_count = 0

    async def failing_refresh(page):
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            raise ConnectionError("Потеряна связь с браузером")
        # На третьем вызове — прерываем цикл через KeyboardInterrupt
        raise KeyboardInterrupt

    with (
        patch("apps.observer_worker.main.refresh_table", side_effect=failing_refresh),
        patch("apps.observer_worker.main.load_offers_from_db", new_callable=AsyncMock),
        patch(
            "apps.observer_worker.main.load_ad_states_from_db",
            new_callable=AsyncMock,
            return_value={},
        ),
        patch("apps.observer_worker.main.asyncio.sleep", new_callable=AsyncMock),
    ):
        with pytest.raises(KeyboardInterrupt):
            await observer_loop(
                page=mock_page,
                offers={},
                telegram_bot_token="",
                telegram_chat_id="",
                interval_seconds=1,
                parse_fn=AsyncMock(),
                browser_manager=mock_browser_manager,
            )

    # Должно быть 2 попытки переподключения (disconnect + connect)
    assert mock_browser_manager.disconnect.call_count == 2
    assert mock_browser_manager.connect.call_count == 2


# Проверяем что после MAX_RECONNECT_ATTEMPTS попыток воркер завершается
@pytest.mark.asyncio
async def test_reconnect_max_attempts_exit():
    """После MAX_RECONNECT_ATTEMPTS ошибок подряд воркер должен завершиться."""
    from apps.observer_worker.main import MAX_RECONNECT_ATTEMPTS, observer_loop

    mock_page = AsyncMock()
    mock_browser_manager = AsyncMock()
    mock_browser_manager.get_page.return_value = mock_page

    # Всегда падаем с ошибкой связи
    async def always_fail(page):
        raise ConnectionError("Потеряна связь с браузером")

    with (
        patch("apps.observer_worker.main.refresh_table", side_effect=always_fail),
        patch("apps.observer_worker.main.load_offers_from_db", new_callable=AsyncMock),
        patch(
            "apps.observer_worker.main.load_ad_states_from_db",
            new_callable=AsyncMock,
            return_value={},
        ),
        patch("apps.observer_worker.main.asyncio.sleep", new_callable=AsyncMock),
    ):
        with pytest.raises(ConnectionError):
            await observer_loop(
                page=mock_page,
                offers={},
                telegram_bot_token="",
                telegram_chat_id="",
                interval_seconds=1,
                parse_fn=AsyncMock(),
                browser_manager=mock_browser_manager,
            )

    # На последней попытке воркер завершается до переподключения,
    # поэтому disconnect вызывается MAX_RECONNECT_ATTEMPTS - 1 раз
    assert mock_browser_manager.disconnect.call_count == MAX_RECONNECT_ATTEMPTS - 1


# Проверяем что успешный цикл сбрасывает счётчик ошибок
@pytest.mark.asyncio
async def test_reconnect_counter_resets_on_success():
    """Успешный цикл должен сбрасывать счётчик ошибок браузера."""
    from apps.observer_worker.main import observer_loop

    mock_page = AsyncMock()
    mock_browser_manager = AsyncMock()
    mock_browser_manager.get_page.return_value = mock_page

    call_count = 0

    async def mixed_refresh(page):
        nonlocal call_count
        call_count += 1
        # Первый вызов — ошибка, второй — успех, третий — прерываем
        if call_count == 1:
            raise ConnectionError("Потеряна связь")
        if call_count == 3:
            raise KeyboardInterrupt
        return True

    mock_parse_fn = AsyncMock(return_value=[])

    with (
        patch("apps.observer_worker.main.refresh_table", side_effect=mixed_refresh),
        patch("apps.observer_worker.main.load_offers_from_db", new_callable=AsyncMock),
        patch(
            "apps.observer_worker.main.load_ad_states_from_db",
            new_callable=AsyncMock,
            return_value={},
        ),
        patch("apps.observer_worker.main.asyncio.sleep", new_callable=AsyncMock),
        patch("apps.observer_worker.main._human_micro_pause", new_callable=AsyncMock),
        patch("apps.observer_worker.main._maybe_macro_pause", new_callable=AsyncMock),
        patch(
            "apps.observer_worker.main.batch_save_snapshots",
            new_callable=AsyncMock,
        ),
    ):
        with pytest.raises(KeyboardInterrupt):
            await observer_loop(
                page=mock_page,
                offers={},
                telegram_bot_token="",
                telegram_chat_id="",
                interval_seconds=1,
                parse_fn=mock_parse_fn,
                browser_manager=mock_browser_manager,
            )

    # Была 1 ошибка, затем успешный цикл сбросил счётчик,
    # поэтому disconnect вызвался только 1 раз (при ошибке)
    assert mock_browser_manager.disconnect.call_count == 1
