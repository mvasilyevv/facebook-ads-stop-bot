# -*- coding: utf-8 -*-
"""Unit-тесты улучшений observer worker: jitter, batch upsert, FSM из БД, reconnect."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.domain import AlertStage, AlertState

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
            "outbound_clicks": 4,
            "outbound_ctr": Decimal("0.5000"),
            "landing_page_views": 3,
            "cost_per_landing_page_view": Decimal("3.3333"),
            "cpm": Decimal("12.5000"),
            "frequency": Decimal("1.2500"),
            "leads": 1,
            "cost_per_lead": Decimal("10.00"),
            "registrations": 0,
            "cost_per_registration": None,
            "deposits": 0,
            "alert_state": AlertState.NORMAL,
            "current_stage": None,
            "early_signal_rule_codes": [],
            "warning_rule_codes": [],
            "stop_rule_codes": [],
            "open_state_token": None,
            "last_observed_at": None,
        }
        for i in range(50)
    ]

    with (
        patch(
            "apps.observer_worker.main.get_session_factory",
            return_value=mock_factory,
        ),
        patch(
            "apps.observer_worker.main._maybe_rollover_cabinet_day",
            new=AsyncMock(),
        ),
    ):
        await batch_save_snapshots(snapshot_data)

    # Должен быть ровно один вызов execute (один INSERT для всех 50 строк)
    assert mock_session.execute.call_count == 1, (
        f"Ожидался 1 вызов execute, получено {mock_session.execute.call_count}"
    )
    # И один commit
    assert mock_session.commit.call_count == 1


# Проверяем что stage EARLY_SIGNAL переводится в состояние EARLY_SIGNAL_SENT
def test_state_for_emitted_stage_maps_early_signal():
    """Ранний сигнал должен отправляться в состоянии EARLY_SIGNAL_SENT."""
    from apps.observer_worker.main import _state_for_emitted_stage

    assert _state_for_emitted_stage(AlertStage.EARLY_SIGNAL) == AlertState.EARLY_SIGNAL_SENT
    assert _state_for_emitted_stage(AlertStage.WARNING) == AlertState.WARNING_SENT
    assert _state_for_emitted_stage(AlertStage.STOP) == AlertState.CLAIMED


# Проверяем склейку текста причины с диагностикой
def test_compose_reason_text_appends_diagnostics_text():
    """Диагностический контекст должен дописываться к основной причине."""
    from apps.observer_worker.main import _compose_reason_text

    assert (
        _compose_reason_text("Основная причина.", "CPM выше медианы.")
        == "Основная причина. CPM выше медианы."
    )
    assert _compose_reason_text("Только причина.", None) == "Только причина."
    assert _compose_reason_text(None, "Только диагностика.") == "Только диагностика."


# Проверяем что напоминание для EARLY_SIGNAL восстанавливает причину из последнего события
@pytest.mark.asyncio
async def test_collect_reminder_alerts_restores_early_signal_reason():
    """Напоминание должно сохранить EARLY_SIGNAL и человекочитаемую причину."""
    from apps.observer_worker.main import _collect_reminder_alerts

    now = datetime.now(UTC)
    snap = SimpleNamespace(
        fb_ad_id="ad_early",
        alert_state=AlertState.EARLY_SIGNAL_SENT,
        snoozed_until=None,
        open_state_token="token_early",
        offer_id=None,
        ad_name="Раннее объявление",
        campaign_name="Campaign",
        adset_name="Adset",
        resolved_offer_code="DRC",
        early_signal_rule_codes=["early_outbound_ctr_signal"],
        warning_rule_codes=[],
        stop_rule_codes=[],
        spend=Decimal("12.34"),
        clicks=7,
        cpc=Decimal("1.76"),
        outbound_clicks=5,
        outbound_ctr=Decimal("0.4200"),
        landing_page_views=4,
        cost_per_landing_page_view=Decimal("3.0850"),
        cpm=Decimal("14.1000"),
        frequency=Decimal("1.4000"),
        leads=0,
        cost_per_lead=None,
        registrations=0,
        cost_per_registration=None,
        deposits=0,
        id=101,
    )
    last_event = SimpleNamespace(
        reason_title="Слабый исходящий CTR",
        reason_text="Сигнал раннего отсечения.",
        metrics_json={"rule_summaries": ["CTR ниже порога"]},
    )

    candidates_result = MagicMock()
    candidates_result.scalars.return_value.all.return_value = [snap]

    last_event_result = MagicMock()
    last_event_result.scalar_one_or_none.return_value = last_event

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.execute = AsyncMock(side_effect=[candidates_result, last_event_result])
    mock_session.scalar = AsyncMock(return_value=now - timedelta(minutes=20))

    mock_factory = MagicMock(return_value=mock_session)

    with patch(
        "apps.observer_worker.main.get_session_factory",
        return_value=mock_factory,
    ):
        reminders = await _collect_reminder_alerts(interval_seconds=90)

    assert len(reminders) == 1
    reminder = reminders[0]
    assert reminder.stage == AlertStage.EARLY_SIGNAL
    assert reminder.matched_rule_codes == ["early_outbound_ctr_signal"]
    assert reminder.reason_title == "Слабый исходящий CTR"
    assert reminder.reason_text == "Сигнал раннего отсечения."
    assert reminder.metrics_json["rule_summaries"] == ["CTR ниже порога"]
    assert reminder.metrics_json["outbound_clicks"] == 5
    assert reminder.metrics_json["frequency"] == "1.4000"


# Проверяем что AlertEvent для раннего сигнала сохраняет причину и состояние
@pytest.mark.asyncio
async def test_send_alerts_to_telegram_persists_early_signal_reason():
    """Отправка раннего сигнала должна сохранить EARLY_SIGNAL_SENT и причину в AlertEvent."""
    from apps.observer_worker.main import _send_alerts_to_telegram

    candidate = MagicMock()
    candidate.snapshot_id = "token-1"
    candidate.fb_ad_id = "ad_early"
    candidate.ad_name = "Раннее объявление"
    candidate.campaign_name = "Campaign"
    candidate.adset_name = "Adset"
    candidate.offer_code = "DRC"
    candidate.stage = AlertStage.EARLY_SIGNAL
    candidate.matched_rule_codes = ["early_outbound_ctr_signal"]
    candidate.reason_title = "Слабый исходящий CTR"
    candidate.reason_text = "Сигнал раннего отсечения."
    candidate.metrics_json = {"spend": "12.34"}
    candidate.offer_id = None

    sent_message = MagicMock()
    sent_message.text = "текст"
    sent_message.reply_markup = None

    fake_client = AsyncMock()
    fake_client.send_message = AsyncMock(return_value={"message_id": 777})

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.add = MagicMock()
    mock_session.commit = AsyncMock()

    mock_factory = MagicMock(return_value=mock_session)

    with (
        patch(
            "apps.observer_worker.main.render_alert_message", return_value=sent_message
        ) as render_mock,
        patch("apps.observer_worker.main.get_session_factory", return_value=mock_factory),
    ):
        await _send_alerts_to_telegram(fake_client, "chat-1", [candidate])

    render_args = render_mock.call_args.kwargs
    rendered_item = render_args["items"][0]
    assert rendered_item.alert_state == AlertState.EARLY_SIGNAL_SENT
    assert rendered_item.reason_title == "Слабый исходящий CTR"
    assert rendered_item.reason_text == "Сигнал раннего отсечения."

    added_event = mock_session.add.call_args.args[0]
    assert added_event.state == AlertState.EARLY_SIGNAL_SENT
    assert added_event.reason_title == "Слабый исходящий CTR"
    assert added_event.reason_text == "Сигнал раннего отсечения."
    assert added_event.telegram_chat_id == "chat-1"
    assert added_event.telegram_message_id == 777
    mock_session.commit.assert_awaited_once()


# Проверяем что при сбое Telegram алерт не сохраняется как доставленный
@pytest.mark.asyncio
async def test_send_alerts_to_telegram_skips_persist_on_failure():
    """Если Telegram не принял сообщение, AlertEvent сохранять нельзя."""
    from apps.observer_worker.main import _send_alerts_to_telegram

    candidate = MagicMock()
    candidate.snapshot_id = "token-2"
    candidate.fb_ad_id = "ad_failed"
    candidate.ad_name = "Проблемный алерт"
    candidate.campaign_name = "Campaign"
    candidate.adset_name = "Adset"
    candidate.offer_code = "DRC"
    candidate.stage = AlertStage.WARNING
    candidate.matched_rule_codes = ["cpl_stop"]
    candidate.reason_title = "Дорогой лид"
    candidate.reason_text = "Цена лида вышла за допустимую границу."
    candidate.metrics_json = {"spend": "12.34"}
    candidate.offer_id = None

    fake_client = AsyncMock()
    fake_client.send_message = AsyncMock(side_effect=RuntimeError("Сбой Telegram"))

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.add = MagicMock()
    mock_session.commit = AsyncMock()

    mock_factory = MagicMock(return_value=mock_session)

    with (
        patch("apps.observer_worker.main.get_session_factory", return_value=mock_factory),
    ):
        await _send_alerts_to_telegram(fake_client, "chat-1", [candidate])

    fake_client.send_message.assert_awaited_once()
    mock_session.add.assert_not_called()
    mock_session.commit.assert_not_called()


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


# Проверяем что Vision-настройки для запуска берутся из БД
@pytest.mark.asyncio
async def test_load_vision_settings_for_runtime_prefers_db():
    """Если в БД есть Vision-настройки, они должны перекрывать fallback env."""
    from apps.observer_worker.main import load_vision_settings_for_runtime

    with patch(
        "apps.observer_worker.main.load_vision_settings_from_db",
        new=AsyncMock(return_value=("db-token", "http://db:3030", "db-profile")),
    ):
        x_token, api_url, profile_id = await load_vision_settings_for_runtime(
            fallback_x_token="env-token",
            fallback_api_url="http://env:3030",
            fallback_profile_id="env-profile",
        )

    assert x_token == "db-token"
    assert api_url == "http://db:3030"
    assert profile_id == "db-profile"


# Проверяем что при пустой БД Vision-настройки берутся из fallback env
@pytest.mark.asyncio
async def test_load_vision_settings_for_runtime_uses_fallback():
    """Если в БД нет Vision-настроек, нужно использовать fallback значения."""
    from apps.observer_worker.main import load_vision_settings_for_runtime

    with patch(
        "apps.observer_worker.main.load_vision_settings_from_db",
        new=AsyncMock(return_value=("", "", "")),
    ):
        x_token, api_url, profile_id = await load_vision_settings_for_runtime(
            fallback_x_token="env-token",
            fallback_api_url="http://env:3030",
            fallback_profile_id="env-profile",
        )

    assert x_token == "env-token"
    assert api_url == "http://env:3030"
    assert profile_id == "env-profile"


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


# Проверяем что reconnect берёт актуальные Vision-настройки из БД
@pytest.mark.asyncio
async def test_reconnect_browser_manager_uses_db_vision_settings():
    """При reconnect должен создаваться новый VisionClient с настройками из БД."""
    from apps.observer_worker.main import reconnect_browser_manager_with_vision_settings

    old_vision = MagicMock()
    old_vision._headers = {"X-Token": "old-token"}
    old_vision._base = "http://old:3030"
    old_vision.close = AsyncMock()

    mock_page = AsyncMock()
    browser_manager = AsyncMock()
    browser_manager._vision = old_vision
    browser_manager._profile_id = "old-profile"
    browser_manager._folder_id = "old-folder"
    browser_manager.get_page = AsyncMock(return_value=mock_page)

    new_vision = MagicMock()

    with (
        patch(
            "apps.observer_worker.main.load_vision_settings_from_db",
            new=AsyncMock(return_value=("db-token", "http://db:3030", "db-profile")),
        ),
        patch("apps.observer_worker.main.VisionClient", return_value=new_vision) as vision_cls,
    ):
        page = await reconnect_browser_manager_with_vision_settings(browser_manager)

    vision_cls.assert_called_once_with(x_token="db-token", base_url="http://db:3030")
    browser_manager.disconnect.assert_awaited_once()
    old_vision.close.assert_awaited_once()
    browser_manager.connect.assert_awaited_once()
    browser_manager.get_page.assert_awaited_once()
    assert browser_manager._vision is new_vision
    assert browser_manager._profile_id == "db-profile"
    assert browser_manager._folder_id is None
    assert page is mock_page


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
