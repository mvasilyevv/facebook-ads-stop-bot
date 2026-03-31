# -*- coding: utf-8 -*-
"""Unit-тесты улучшений observer worker: jitter, batch upsert, FSM из БД, reconnect."""

from __future__ import annotations

import asyncio
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.domain import AlertStage, AlertState
from core.telegram.service import TelegramDestination


def _scalars_result(rows):
    """Создаёт мок результата SQLAlchemy для scalars().all()."""
    result = MagicMock()
    result.scalars.return_value.all.return_value = rows
    return result


def _telegram_destination(
    *,
    chat_id: str = "chat-1",
    delivery_mode: str = "PRIVATE_CHAT",
    early_topic_id: int | None = None,
    warning_topic_id: int | None = None,
    stop_topic_id: int | None = None,
) -> TelegramDestination:
    """Собирает тестовый destination для доставки Telegram-алертов."""
    return TelegramDestination(
        chat_id=chat_id,
        telegram_user_id="42",
        role="owner",
        username="owner",
        first_name="Иван",
        is_primary=True,
        delivery_mode=delivery_mode,
        control_topic_id=11 if delivery_mode == "FORUM_GROUP" else None,
        early_topic_id=early_topic_id,
        warning_topic_id=warning_topic_id,
        stop_topic_id=stop_topic_id,
        enable_topic_id=15 if delivery_mode == "FORUM_GROUP" else None,
    )


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
    import apps.observer_worker.main as observer_main
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
        patch.object(observer_main, "_PENDING_ZERO_SCAN_CONFIRMATION_AT", None),
        patch.object(observer_main, "_PENDING_PARTIAL_BATCH_CONFIRMATION_AT", None),
        patch.object(observer_main, "_LAST_ACCEPTED_SNAPSHOT_BATCH_SIZE", None),
    ):
        await batch_save_snapshots(snapshot_data)

    # Должен быть ровно один вызов execute (один INSERT для всех 50 строк)
    assert mock_session.execute.call_count == 1, (
        f"Ожидался 1 вызов execute, получено {mock_session.execute.call_count}"
    )
    # И один commit
    assert mock_session.commit.call_count == 1


# Проверяем что пустые campaign/adset не затирают уже сохранённые названия
@pytest.mark.asyncio
async def test_batch_save_snapshots_preserves_identity_names_on_empty_update():
    """Upsert не должен перезаписывать campaign/adset пустыми строками."""
    import apps.observer_worker.main as observer_main
    from apps.observer_worker.main import batch_save_snapshots

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_factory = MagicMock(return_value=mock_session)

    snapshot_data = [
        {
            "fb_ad_id": "ad_1",
            "campaign_name": "",
            "adset_name": "",
            "ad_name": "ad_1",
            "delivery_status": "OFF",
            "offer_id": None,
            "resolved_offer_code": None,
            "spend": Decimal("0.85"),
            "clicks": 25,
            "cpc": Decimal("0.03"),
            "outbound_clicks": 14,
            "outbound_ctr": Decimal("0.9500"),
            "landing_page_views": 4,
            "cost_per_landing_page_view": Decimal("0.2125"),
            "cpm": Decimal("12.5000"),
            "frequency": Decimal("1.2500"),
            "leads": 1,
            "cost_per_lead": Decimal("0.85"),
            "registrations": 2,
            "cost_per_registration": Decimal("0.4250"),
            "deposits": 0,
            "alert_state": AlertState.NORMAL,
            "current_stage": None,
            "early_signal_rule_codes": [],
            "warning_rule_codes": [],
            "stop_rule_codes": [],
            "open_state_token": None,
            "last_observed_at": None,
        }
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
        patch.object(observer_main, "_PENDING_ZERO_SCAN_CONFIRMATION_AT", None),
        patch.object(observer_main, "_PENDING_PARTIAL_BATCH_CONFIRMATION_AT", None),
        patch.object(observer_main, "_LAST_ACCEPTED_SNAPSHOT_BATCH_SIZE", None),
    ):
        await batch_save_snapshots(snapshot_data)

    sql = str(mock_session.execute.await_args.args[0]).lower()
    assert "coalesce" in sql
    assert "nullif" in sql
    assert "campaign_name" in sql
    assert "adset_name" in sql


# Проверяем что первый полный zero-scan не затирает живой батч без повторного подтверждения.
@pytest.mark.asyncio
async def test_batch_save_snapshots_requires_confirmed_zero_scan_before_persist():
    """Подозрительный zero-scan должен пропускаться один цикл и применяться только после повтора."""
    import apps.observer_worker.main as observer_main

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_factory = MagicMock(return_value=mock_session)

    snapshot_data = [
        {
            "fb_ad_id": "ad_1",
            "campaign_name": "campaign",
            "adset_name": "adset",
            "ad_name": "ad_1",
            "delivery_status": "ACTIVE",
            "offer_id": None,
            "resolved_offer_code": None,
            "spend": Decimal("0"),
            "clicks": 0,
            "cpc": None,
            "outbound_clicks": 0,
            "outbound_ctr": None,
            "landing_page_views": 0,
            "cost_per_landing_page_view": None,
            "cpm": None,
            "frequency": None,
            "leads": 0,
            "cost_per_lead": None,
            "registrations": 0,
            "cost_per_registration": None,
            "deposits": 0,
            "alert_state": AlertState.NORMAL,
            "current_stage": None,
            "early_signal_rule_codes": [],
            "warning_rule_codes": [],
            "stop_rule_codes": [],
            "open_state_token": None,
            "last_observed_at": datetime.now(UTC),
        }
    ]

    with (
        patch(
            "apps.observer_worker.main.get_session_factory",
            return_value=mock_factory,
        ),
        patch(
            "apps.observer_worker.main._maybe_rollover_cabinet_day",
            new=AsyncMock(),
        ) as rollover_mock,
        patch.object(observer_main, "_PENDING_ZERO_SCAN_CONFIRMATION_AT", None),
    ):
        await observer_main.batch_save_snapshots(snapshot_data)
        await observer_main.batch_save_snapshots(snapshot_data)

    assert mock_session.execute.call_count == 1
    assert mock_session.commit.call_count == 1
    rollover_mock.assert_awaited_once()


# Проверяем что резкое проседание количества строк не затирает live-срез без подтверждения.
@pytest.mark.asyncio
async def test_batch_save_snapshots_requires_confirmed_partial_batch_before_persist():
    """Первый подозрительно неполный non-zero батч должен пропускаться до повторного подтверждения."""
    import apps.observer_worker.main as observer_main

    def build_snapshot(index: int) -> dict:
        return {
            "fb_ad_id": f"ad_{index}",
            "campaign_name": "campaign",
            "adset_name": "adset",
            "ad_name": f"ad_{index}",
            "delivery_status": "ACTIVE",
            "offer_id": None,
            "resolved_offer_code": None,
            "spend": Decimal("1"),
            "clicks": 1,
            "cpc": Decimal("1"),
            "outbound_clicks": 0,
            "outbound_ctr": None,
            "landing_page_views": 0,
            "cost_per_landing_page_view": None,
            "cpm": None,
            "frequency": None,
            "leads": 0,
            "cost_per_lead": None,
            "registrations": 0,
            "cost_per_registration": None,
            "deposits": 0,
            "alert_state": AlertState.NORMAL,
            "current_stage": None,
            "early_signal_rule_codes": [],
            "warning_rule_codes": [],
            "stop_rule_codes": [],
            "open_state_token": None,
            "last_observed_at": datetime.now(UTC),
        }

    full_snapshot_data = [build_snapshot(index) for index in range(30)]
    partial_snapshot_data = [build_snapshot(index) for index in range(18)]

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_factory = MagicMock(return_value=mock_session)

    with (
        patch(
            "apps.observer_worker.main.get_session_factory",
            return_value=mock_factory,
        ),
        patch(
            "apps.observer_worker.main._maybe_rollover_cabinet_day",
            new=AsyncMock(),
        ),
        patch.object(observer_main, "_PENDING_ZERO_SCAN_CONFIRMATION_AT", None),
        patch.object(observer_main, "_PENDING_PARTIAL_BATCH_CONFIRMATION_AT", None),
        patch.object(observer_main, "_LAST_ACCEPTED_SNAPSHOT_BATCH_SIZE", None),
    ):
        await observer_main.batch_save_snapshots(full_snapshot_data)
        await observer_main.batch_save_snapshots(partial_snapshot_data)
        await observer_main.batch_save_snapshots(partial_snapshot_data)

    assert mock_session.execute.call_count == 2
    assert mock_session.commit.call_count == 2


# Проверяем что stage EARLY_SIGNAL переводится в состояние EARLY_SIGNAL_SENT
def test_state_for_emitted_stage_maps_early_signal():
    """Ранний сигнал должен отправляться в состоянии EARLY_SIGNAL_SENT."""
    from apps.observer_worker.main import _state_for_emitted_stage

    assert _state_for_emitted_stage(AlertStage.EARLY_SIGNAL) == AlertState.EARLY_SIGNAL_SENT
    assert _state_for_emitted_stage(AlertStage.WARNING) == AlertState.WARNING_SENT
    assert _state_for_emitted_stage(AlertStage.STOP) == AlertState.CLAIMED


# Проверяем что CLAIMED ждёт подтверждения OFF, а DISABLED снимается только при новой активации
def test_reopen_reactivated_alert_state_keeps_claimed_and_resets_disabled():
    """CLAIMED не должен сбрасываться до подтверждения OFF следующим сканом."""
    from apps.observer_worker.main import reopen_reactivated_alert_state

    assert reopen_reactivated_alert_state(AlertState.CLAIMED, "token-1", "ACTIVE") == (
        AlertState.CLAIMED,
        "token-1",
    )
    assert reopen_reactivated_alert_state(AlertState.DISABLED, "token-2", "ACTIVE") == (
        AlertState.NORMAL,
        None,
    )
    assert reopen_reactivated_alert_state(AlertState.CLAIMED, "token-3", "OFF") == (
        AlertState.CLAIMED,
        "token-3",
    )
    assert reopen_reactivated_alert_state(AlertState.STOP_SENT, "token-4", "ACTIVE") == (
        AlertState.STOP_SENT,
        "token-4",
    )


# Проверяем что недавний SUCCEEDED не даёт observer сразу запускать тихий автоповтор.
@pytest.mark.asyncio
async def test_reconcile_disable_incidents_after_scan_keeps_recent_success():
    """Недавний успешный disable-task должен оставлять incident без нового follow-up."""
    from apps.observer_worker.main import reconcile_disable_incidents_after_scan

    snapshot = SimpleNamespace(
        fb_ad_id="ad_001",
        delivery_status="UNKNOWN",
        alert_state=AlertState.CLAIMED,
        current_stage=AlertStage.STOP,
        open_state_token="incident-001",
        offer_id=None,
        ad_name="Тестовое объявление",
        campaign_name="Campaign",
        adset_name="Adset",
        resolved_offer_code="DRC",
        stop_rule_codes=["cpc_stop"],
        warning_rule_codes=[],
        early_signal_rule_codes=[],
        spend=Decimal("12.34"),
        clicks=3,
        cpc=Decimal("4.11"),
        outbound_clicks=2,
        outbound_ctr=Decimal("0.1200"),
        landing_page_views=1,
        cost_per_landing_page_view=Decimal("12.3400"),
        cpm=Decimal("10.0000"),
        frequency=Decimal("1.1000"),
        leads=0,
        cost_per_lead=None,
        registrations=0,
        cost_per_registration=None,
        deposits=0,
        last_observed_at=datetime.now(UTC),
    )
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.execute = AsyncMock(return_value=_scalars_result([snapshot]))
    mock_session.scalar = AsyncMock(side_effect=[datetime.now(UTC), 0, 1])
    mock_factory = MagicMock(return_value=mock_session)

    with patch("apps.observer_worker.main.get_session_factory", return_value=mock_factory):
        alerts = await reconcile_disable_incidents_after_scan()

    assert alerts == []
    mock_session.commit.assert_not_awaited()


# Проверяем что исчерпанный grace создаёт тихий follow-up disable без нового STOP-алерта.
@pytest.mark.asyncio
async def test_reconcile_disable_incidents_after_scan_creates_follow_up_attempt():
    """Если OFF не подтвердился, observer должен создать новую auto-disable попытку в том же incident."""
    from apps.observer_worker.main import reconcile_disable_incidents_after_scan

    snapshot = SimpleNamespace(
        fb_ad_id="ad_002",
        delivery_status="UNKNOWN",
        alert_state=AlertState.CLAIMED,
        current_stage=AlertStage.STOP,
        open_state_token="incident-002",
        offer_id=None,
        ad_name="Тестовое объявление 2",
        campaign_name="Campaign",
        adset_name="Adset",
        resolved_offer_code="DRC",
        stop_rule_codes=["cpl_stop"],
        warning_rule_codes=[],
        early_signal_rule_codes=[],
        spend=Decimal("18.00"),
        clicks=4,
        cpc=Decimal("4.50"),
        outbound_clicks=2,
        outbound_ctr=Decimal("0.1200"),
        landing_page_views=1,
        cost_per_landing_page_view=Decimal("18.0000"),
        cpm=Decimal("11.0000"),
        frequency=Decimal("1.3000"),
        leads=0,
        cost_per_lead=None,
        registrations=0,
        cost_per_registration=None,
        deposits=0,
        last_observed_at=datetime.now(UTC),
    )
    latest_task = SimpleNamespace(last_error=None)
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.execute = AsyncMock(return_value=_scalars_result([snapshot]))
    mock_session.scalar = AsyncMock(side_effect=[datetime.now(UTC), 0, 0, 1, latest_task])
    mock_factory = MagicMock(return_value=mock_session)

    with (
        patch("apps.observer_worker.main.get_session_factory", return_value=mock_factory),
        patch(
            "apps.observer_worker.main._create_auto_disable_task_for_snapshot",
            new=AsyncMock(return_value=True),
        ) as create_attempt,
    ):
        alerts = await reconcile_disable_incidents_after_scan()

    assert alerts == []
    create_attempt.assert_awaited_once()
    mock_session.commit.assert_awaited_once()


# Проверяем что после лимита тихих автоповторов инцидент уходит в ручной разбор без новой задачи.
@pytest.mark.asyncio
async def test_reconcile_disable_incidents_after_scan_marks_manual_attention_after_limit():
    """После лимита follow-up попыток observer должен только обновить инцидент сообщением ручного разбора."""
    from apps.observer_worker.main import reconcile_disable_incidents_after_scan

    snapshot = SimpleNamespace(
        fb_ad_id="ad_003",
        delivery_status="UNKNOWN",
        alert_state=AlertState.CLAIMED,
        current_stage=AlertStage.STOP,
        open_state_token="incident-003",
        offer_id=None,
        ad_name="Тестовое объявление 3",
        campaign_name="Campaign",
        adset_name="Adset",
        resolved_offer_code="DRC",
        stop_rule_codes=["cpr_stop"],
        warning_rule_codes=[],
        early_signal_rule_codes=[],
        spend=Decimal("21.00"),
        clicks=5,
        cpc=Decimal("4.20"),
        outbound_clicks=3,
        outbound_ctr=Decimal("0.1300"),
        landing_page_views=1,
        cost_per_landing_page_view=Decimal("21.0000"),
        cpm=Decimal("13.0000"),
        frequency=Decimal("1.5000"),
        leads=0,
        cost_per_lead=None,
        registrations=0,
        cost_per_registration=None,
        deposits=0,
        last_observed_at=datetime.now(UTC),
    )
    latest_task = SimpleNamespace(last_error="Meta долго не подтверждает OFF")
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.execute = AsyncMock(return_value=_scalars_result([snapshot]))
    mock_session.scalar = AsyncMock(side_effect=[datetime.now(UTC), 0, 0, 4, latest_task])
    mock_factory = MagicMock(return_value=mock_session)

    with (
        patch("apps.observer_worker.main.get_session_factory", return_value=mock_factory),
        patch(
            "apps.observer_worker.main._create_auto_disable_task_for_snapshot",
            new=AsyncMock(return_value=True),
        ) as create_attempt,
    ):
        alerts = await reconcile_disable_incidents_after_scan()

    assert len(alerts) == 1
    assert alerts[0].snapshot_id == "incident-003"
    assert alerts[0].persist_event is False
    assert "ручную" in (alerts[0].reason_text or "")
    create_attempt.assert_not_awaited()
    mock_session.commit.assert_not_awaited()


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
        last_observed_at=now - timedelta(minutes=2),
    )
    last_event = SimpleNamespace(
        reason_title="Слабый исходящий CTR",
        reason_text="Сигнал раннего отсечения.",
        metrics_json={
            "rule_summaries": ["CTR ниже порога"],
            "traffic_diagnostics": {
                "summary_text": "Трафик начал дорожать.",
                "cpm": {"status": "critical", "text": "CPM выше недавней медианы."},
            },
        },
    )

    candidates_result = MagicMock()
    candidates_result.scalars.return_value.all.return_value = [snap]

    last_event_result = MagicMock()
    last_event_result.scalar_one_or_none.return_value = last_event

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.execute = AsyncMock(side_effect=[candidates_result, last_event_result])
    mock_session.scalar = AsyncMock(side_effect=[now, now - timedelta(minutes=20)])

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
    assert reminder.metrics_json["traffic_diagnostics"]["summary_text"] == "Трафик начал дорожать."
    assert reminder.metrics_json["outbound_clicks"] == 5
    assert reminder.metrics_json["frequency"] == "1.4000"


# Проверяем, что snooze не подавляет STOP-напоминание.
@pytest.mark.asyncio
async def test_collect_reminder_alerts_keeps_stop_even_if_snoozed():
    """STOP-напоминание должно пройти даже при активном snoozed_until."""
    from apps.observer_worker.main import _collect_reminder_alerts

    now = datetime.now(UTC)
    snap = SimpleNamespace(
        fb_ad_id="ad_stop",
        alert_state=AlertState.STOP_SENT,
        snoozed_until=now + timedelta(hours=2),
        open_state_token="token_stop",
        offer_id=None,
        ad_name="STOP объявление",
        campaign_name="Campaign",
        adset_name="Adset",
        resolved_offer_code="DRC",
        early_signal_rule_codes=[],
        warning_rule_codes=[],
        stop_rule_codes=["cpc_stop"],
        spend=Decimal("30.00"),
        clicks=3,
        cpc=Decimal("10.00"),
        outbound_clicks=2,
        outbound_ctr=Decimal("0.2500"),
        landing_page_views=1,
        cost_per_landing_page_view=Decimal("30.0000"),
        cpm=Decimal("15.0000"),
        frequency=Decimal("1.6000"),
        leads=0,
        cost_per_lead=None,
        registrations=0,
        cost_per_registration=None,
        deposits=0,
        id=303,
        last_observed_at=now - timedelta(minutes=1),
    )
    last_event = SimpleNamespace(
        reason_title="Стоп без подтверждения OFF",
        reason_text="Нужно проверить отключение вручную.",
        metrics_json={"rule_summaries": ["CPC выше стопа"]},
    )

    candidates_result = MagicMock()
    candidates_result.scalars.return_value.all.return_value = [snap]

    last_event_result = MagicMock()
    last_event_result.scalar_one_or_none.return_value = last_event

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.execute = AsyncMock(side_effect=[candidates_result, last_event_result])
    mock_session.scalar = AsyncMock(side_effect=[now, now - timedelta(minutes=20)])
    mock_factory = MagicMock(return_value=mock_session)

    with patch("apps.observer_worker.main.get_session_factory", return_value=mock_factory):
        reminders = await _collect_reminder_alerts(interval_seconds=90)

    assert len(reminders) == 1
    assert reminders[0].stage == AlertStage.STOP


# Проверяем что архивные объявления не попадают в повторные WARNING-напоминания
@pytest.mark.asyncio
async def test_collect_reminder_alerts_skips_archived_snapshots():
    """Напоминания должны отправляться только по объявлениям из актуальной скан-сессии."""
    from apps.observer_worker.main import _collect_reminder_alerts

    now = datetime.now(UTC)
    archived_snap = SimpleNamespace(
        fb_ad_id="ad_archived",
        alert_state=AlertState.WARNING_SENT,
        snoozed_until=None,
        open_state_token="token_archived",
        offer_id=None,
        ad_name="Архивное объявление",
        campaign_name="Campaign",
        adset_name="Adset",
        resolved_offer_code="DRC",
        early_signal_rule_codes=[],
        warning_rule_codes=["cpl_stop"],
        stop_rule_codes=[],
        spend=Decimal("18.00"),
        clicks=9,
        cpc=Decimal("2.00"),
        outbound_clicks=7,
        outbound_ctr=Decimal("0.3100"),
        landing_page_views=4,
        cost_per_landing_page_view=Decimal("4.5000"),
        cpm=Decimal("15.0000"),
        frequency=Decimal("1.2000"),
        leads=1,
        cost_per_lead=Decimal("18.0000"),
        registrations=0,
        cost_per_registration=None,
        deposits=0,
        id=202,
        last_observed_at=now - timedelta(minutes=45),
    )

    candidates_result = MagicMock()
    candidates_result.scalars.return_value.all.return_value = [archived_snap]

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.execute = AsyncMock(return_value=candidates_result)
    mock_session.scalar = AsyncMock(side_effect=[now])

    mock_factory = MagicMock(return_value=mock_session)

    with patch(
        "apps.observer_worker.main.get_session_factory",
        return_value=mock_factory,
    ):
        reminders = await _collect_reminder_alerts(interval_seconds=90)

    assert reminders == []
    mock_session.execute.assert_awaited_once()
    mock_session.scalar.assert_awaited_once()


# Проверяем что авто-стоп не ставит disable-задачу для архивного объявления
@pytest.mark.asyncio
async def test_auto_create_disable_tasks_skips_archived_snapshot():
    """Авто-стоп должен пропускать snapshot, который уже выпал из актуального окна."""
    from apps.observer_worker.main import auto_create_disable_tasks

    now = datetime.now(UTC)
    alert = SimpleNamespace(
        fb_ad_id="ad_archived",
        ad_name="Архивный стоп",
        snapshot_id="token-stop",
    )
    snapshot = SimpleNamespace(
        id="snapshot-1",
        offer_id=None,
        last_observed_at=now - timedelta(minutes=45),
    )

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.scalar = AsyncMock(side_effect=[now, snapshot])
    mock_session.execute = AsyncMock()
    mock_factory = MagicMock(return_value=mock_session)

    with patch(
        "apps.observer_worker.main.get_session_factory",
        return_value=mock_factory,
    ):
        await auto_create_disable_tasks([alert])

    mock_session.execute.assert_not_awaited()
    mock_session.commit.assert_not_awaited()


# Проверяем что AlertEvent для раннего сигнала сохраняет причину и состояние
@pytest.mark.asyncio
async def test_send_alerts_to_telegram_persists_early_signal_reason():
    """Отправка раннего сигнала должна сохранить EARLY_SIGNAL_SENT и причину в AlertEvent."""
    from apps.observer_worker.main import _send_alerts_to_telegram

    destination = _telegram_destination(chat_id="chat-1")
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
    snapshot = SimpleNamespace(
        id="snapshot-1",
        telegram_group_key=None,
        telegram_chat_id=None,
        telegram_message_id=None,
    )

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.add = MagicMock()
    mock_session.commit = AsyncMock()
    mock_session.scalar = AsyncMock(side_effect=[snapshot, None])

    mock_factory = MagicMock(return_value=mock_session)

    with (
        patch(
            "apps.observer_worker.main.render_alert_message", return_value=sent_message
        ) as render_mock,
        patch("apps.observer_worker.main.get_session_factory", return_value=mock_factory),
        patch(
            "apps.observer_worker.main.load_message_refs_by_chat",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "apps.observer_worker.main.upsert_message_ref",
            new=AsyncMock(),
        ) as upsert_ref,
    ):
        await _send_alerts_to_telegram(fake_client, destination, [candidate])

    render_args = render_mock.call_args.kwargs
    rendered_item = render_args["items"][0]
    assert rendered_item.alert_state == AlertState.EARLY_SIGNAL_SENT
    assert rendered_item.reason_title == "Слабый исходящий CTR"
    assert rendered_item.reason_text == "Сигнал раннего отсечения."

    added_event = mock_session.add.call_args.args[0]
    assert added_event.state == AlertState.EARLY_SIGNAL_SENT
    assert added_event.reason_title == "Слабый исходящий CTR"
    assert added_event.reason_text == "Сигнал раннего отсечения."
    assert added_event.telegram_chat_id == destination.chat_id
    assert added_event.telegram_message_id == 777
    assert added_event.telegram_group_key == "token-1"
    upsert_ref.assert_awaited_once()
    mock_session.commit.assert_awaited_once()


# Проверяем что при сбое Telegram алерт не сохраняется как доставленный
@pytest.mark.asyncio
async def test_send_alerts_to_telegram_skips_persist_on_failure():
    """Если Telegram не принял сообщение, AlertEvent сохранять нельзя."""
    from apps.observer_worker.main import _send_alerts_to_telegram

    destination = _telegram_destination(chat_id="chat-1")
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
    mock_session.scalar = AsyncMock(return_value=None)

    mock_factory = MagicMock(return_value=mock_session)

    with (
        patch("apps.observer_worker.main.get_session_factory", return_value=mock_factory),
        patch(
            "apps.observer_worker.main.load_message_refs_by_chat",
            new=AsyncMock(return_value={}),
        ),
    ):
        await _send_alerts_to_telegram(fake_client, destination, [candidate])

    fake_client.send_message.assert_awaited_once()
    mock_session.add.assert_not_called()
    mock_session.commit.assert_not_called()


# Проверяем что обновление того же incident не создаёт новый AlertEvent повторно.
@pytest.mark.asyncio
async def test_send_alerts_to_telegram_updates_same_incident_without_new_history_row():
    """Повторное обновление одного incident должно редактировать сообщение без новой history-записи."""
    from apps.observer_worker.main import _send_alerts_to_telegram

    destination = _telegram_destination(
        chat_id="chat-1",
        delivery_mode="FORUM_GROUP",
        stop_topic_id=14,
    )
    candidate = MagicMock()
    candidate.snapshot_id = "incident-777"
    candidate.fb_ad_id = "ad_same_incident"
    candidate.ad_name = "Повторный инцидент"
    candidate.campaign_name = "Campaign"
    candidate.adset_name = "Adset"
    candidate.offer_code = "DRC"
    candidate.stage = AlertStage.STOP
    candidate.matched_rule_codes = ["cpc_stop"]
    candidate.reason_title = "Нужна ручная проверка отключения"
    candidate.reason_text = "Бот выполнил 3 тихих автоповтора без подтверждения OFF."
    candidate.metrics_json = {"spend": "30.00"}
    candidate.offer_id = None
    candidate.persist_event = False

    sent_message = MagicMock()
    sent_message.text = "обновлённый текст"
    sent_message.reply_markup = None

    snapshot = SimpleNamespace(
        id="snapshot-777",
        telegram_group_key=None,
        telegram_chat_id=None,
        telegram_message_id=None,
    )
    existing_stage_event = SimpleNamespace(
        snapshot_id=None,
        offer_id=None,
        ad_name="Старое имя",
        matched_rule_codes=[],
        reason_title=None,
        reason_text=None,
        metrics_json={},
        message_text="старый текст",
        telegram_message_id=321,
    )

    fake_client = AsyncMock()
    fake_client.edit_message = AsyncMock()

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.add = MagicMock()
    mock_session.commit = AsyncMock()
    mock_session.scalar = AsyncMock(side_effect=[snapshot, existing_stage_event])
    mock_factory = MagicMock(return_value=mock_session)

    with (
        patch(
            "apps.observer_worker.main.render_alert_message",
            return_value=sent_message,
        ),
        patch("apps.observer_worker.main.get_session_factory", return_value=mock_factory),
        patch(
            "apps.observer_worker.main.load_message_refs_by_chat",
            new=AsyncMock(return_value={"chat-1": 321}),
        ),
        patch(
            "apps.observer_worker.main.upsert_message_ref",
            new=AsyncMock(),
        ) as upsert_ref,
    ):
        await _send_alerts_to_telegram(fake_client, destination, [candidate])

    fake_client.edit_message.assert_awaited_once()
    assert fake_client.edit_message.await_args.kwargs["message_thread_id"] == 14
    mock_session.add.assert_not_called()
    assert existing_stage_event.reason_title == "Нужна ручная проверка отключения"
    assert existing_stage_event.telegram_message_id == 321
    upsert_ref.assert_awaited_once()


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


# Проверяем что отложенный retry не должен ставить observer на паузу раньше времени
@pytest.mark.asyncio
async def test_get_disable_queue_pause_reason_ignores_future_retry_only_queue():
    """Если в очереди остались только будущие RETRYING-задачи, сканирование не должно стопориться."""
    from apps.observer_worker.main import get_disable_queue_pause_reason
    from core.domain import DisableTaskStatus

    mock_result = MagicMock()
    mock_result.all.return_value = [
        (DisableTaskStatus.RETRYING, datetime.now(UTC) + timedelta(minutes=3)),
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


# Проверяем что reconnect ловит только профильные browser-ошибки
def test_is_browser_connection_error_filters_runtime_errors():
    """Reconnect-контур не должен маскировать произвольные RuntimeError."""
    from apps.observer_worker.main import PatchrightError, _is_browser_connection_error

    assert _is_browser_connection_error(ConnectionError("Потеряна связь"))
    assert _is_browser_connection_error(
        RuntimeError("Target page, context or browser has been closed")
    )
    assert _is_browser_connection_error(PatchrightError("Patchright отключился"))
    assert not _is_browser_connection_error(RuntimeError("Сбой Telegram"))


# Проверяем что таблица принудительно возвращается к началу перед новым циклом
@pytest.mark.asyncio
async def test_reset_ads_table_scroll_rewinds_table_to_top():
    """Observer должен уводить скролл таблицы вверх перед новым сканом."""
    from apps.observer_worker.main import _reset_ads_table_scroll

    page = AsyncMock()
    page.viewport_size = {"width": 1200, "height": 800}
    page.evaluate = AsyncMock(return_value=2)
    page.mouse = AsyncMock()
    page.mouse.move = AsyncMock()
    page.mouse.wheel = AsyncMock()

    with (
        patch("apps.observer_worker.main.human_move", new=AsyncMock()) as human_move_mock,
        patch(
            "apps.observer_worker.main.human_wheel_scroll",
            new=AsyncMock(),
        ) as human_wheel_scroll_mock,
    ):
        await _reset_ads_table_scroll(page)

    human_move_mock.assert_awaited_once_with(page, 600.0, 400.0)
    page.evaluate.assert_awaited_once()
    assert human_wheel_scroll_mock.await_count == 4
    for call in human_wheel_scroll_mock.await_args_list:
        assert call.args == (page, -1200)
        assert call.kwargs["anchor"] == (600.0, 400.0)
        assert call.kwargs["move_before"] is False


def _patch_observer_loop_runtime(stack: ExitStack, *, scan_side_effect) -> AsyncMock:
    """Изолирует observer_loop от БД и внешних интеграций."""
    stack.enter_context(
        patch("apps.observer_worker.main.load_offers_from_db", new=AsyncMock(return_value={}))
    )
    stack.enter_context(
        patch(
            "apps.observer_worker.main.load_ad_states_from_db",
            new=AsyncMock(return_value={}),
        )
    )
    stack.enter_context(
        patch(
            "apps.observer_worker.main.load_telegram_settings_from_db",
            new=AsyncMock(return_value=("", [])),
        )
    )
    stack.enter_context(
        patch(
            "apps.observer_worker.main.load_observer_settings_from_db",
            new=AsyncMock(return_value=(1, 0, Decimal("80"), Decimal("100"))),
        )
    )
    stack.enter_context(
        patch(
            "apps.observer_worker.main.reconcile_disable_tasks_in_db",
            new=AsyncMock(),
        )
    )
    stack.enter_context(
        patch(
            "apps.observer_worker.main.reconcile_disable_incidents_after_scan",
            new=AsyncMock(return_value=[]),
        )
    )
    stack.enter_context(
        patch(
            "apps.observer_worker.main.refresh_runtime_ad_states",
            new=AsyncMock(side_effect=lambda states: states),
        )
    )
    stack.enter_context(
        patch(
            "apps.observer_worker.main.check_scanning_enabled",
            new=AsyncMock(return_value=True),
        )
    )
    stack.enter_context(
        patch(
            "apps.observer_worker.main.get_disable_queue_pause_reason",
            new=AsyncMock(return_value=None),
        )
    )
    stack.enter_context(
        patch(
            "apps.observer_worker.main.check_vision_reconnect_flag",
            new=AsyncMock(return_value=False),
        )
    )
    stack.enter_context(
        patch(
            "apps.observer_worker.main.scan_ads_with_page_recovery",
            new=AsyncMock(side_effect=scan_side_effect),
        )
    )
    stack.enter_context(patch("apps.observer_worker.main.batch_save_snapshots", new=AsyncMock()))
    stack.enter_context(
        patch("apps.observer_worker.main.auto_create_disable_tasks", new=AsyncMock())
    )
    stack.enter_context(
        patch(
            "apps.observer_worker.main._collect_reminder_alerts",
            new=AsyncMock(return_value=[]),
        )
    )
    stack.enter_context(patch("apps.observer_worker.main._human_micro_pause", new=AsyncMock()))
    stack.enter_context(
        patch("apps.observer_worker.main.broadcast_observer_runtime_message", new=AsyncMock())
    )
    stack.enter_context(
        patch("apps.observer_worker.main.update_observer_runtime_status", new=AsyncMock())
    )
    stack.enter_context(
        patch(
            "apps.observer_worker.main.check_scan_requested_flag",
            new=AsyncMock(return_value=False),
        )
    )
    stack.enter_context(patch("apps.observer_worker.main.random.uniform", return_value=0))
    stack.enter_context(patch("apps.observer_worker.main.compute_jitter", return_value=0))
    return stack.enter_context(
        patch("apps.observer_worker.main.asyncio.sleep", new_callable=AsyncMock)
    )


# Проверяем что observer делегирует скан отдельному recovery-helper
@pytest.mark.asyncio
async def test_observer_loop_delegates_scan_to_recovery_helper():
    """Каждый цикл должен вызывать отдельный helper восстановления скана."""
    from apps.observer_worker.main import observer_loop

    mock_page = AsyncMock()
    mock_page.viewport_size = {"width": 1200, "height": 800}
    shutdown_event = asyncio.Event()
    parse_fn = AsyncMock(return_value=[])

    async def scan_and_stop(**kwargs):
        shutdown_event.set()
        return []

    scan_mock = AsyncMock(side_effect=scan_and_stop)

    with ExitStack() as stack:
        _patch_observer_loop_runtime(stack, scan_side_effect=AsyncMock(return_value=[]))
        stack.enter_context(
            patch(
                "apps.observer_worker.main.scan_ads_with_page_recovery",
                new=scan_mock,
            )
        )
        await observer_loop(
            page=mock_page,
            offers={},
            telegram_bot_token="",
            telegram_chat_id="",
            interval_seconds=1,
            parse_fn=parse_fn,
            browser_manager=AsyncMock(),
            shutdown_event=shutdown_event,
        )

    scan_mock.assert_awaited_once()
    assert scan_mock.await_args.kwargs["page"] is mock_page
    assert scan_mock.await_args.kwargs["parse_fn"] is parse_fn
    assert callable(scan_mock.await_args.kwargs["refresh_table_fn"])
    assert callable(scan_mock.await_args.kwargs["reset_scroll_fn"])
    assert callable(scan_mock.await_args.kwargs["scroll_and_parse_fn"])


# Проверяем что после 5 неудачных попыток observer выключает сканирование и шлёт служебный TG-алерт.
@pytest.mark.asyncio
async def test_observer_loop_disables_scanning_after_scan_recovery_exhausted():
    """После исчерпания recovery observer должен выключить сканирование и отправить alert."""
    from apps.observer_worker.main import observer_loop
    from core.scanner.recovery import ScanDataUnavailableError

    shutdown_event = asyncio.Event()

    with ExitStack() as stack:
        _patch_observer_loop_runtime(stack, scan_side_effect=AsyncMock(return_value=[]))
        broadcast_mock = stack.enter_context(
            patch(
                "apps.observer_worker.main.broadcast_observer_runtime_message",
                new=AsyncMock(),
            )
        )
        set_scanning_mock = stack.enter_context(
            patch(
                "apps.observer_worker.main.set_observer_scanning_enabled",
                new=AsyncMock(side_effect=lambda enabled: shutdown_event.set()),
            )
        )
        update_status_mock = stack.enter_context(
            patch(
                "apps.observer_worker.main.update_observer_runtime_status",
                new=AsyncMock(),
            )
        )
        stack.enter_context(
            patch(
                "apps.observer_worker.main.scan_ads_with_page_recovery",
                new=AsyncMock(
                    side_effect=ScanDataUnavailableError(
                        attempts=5,
                        retry_interval_seconds=60,
                    )
                ),
            )
        )

        await observer_loop(
            page=AsyncMock(),
            offers={},
            telegram_bot_token="fallback-token",
            telegram_chat_id="fallback-chat",
            interval_seconds=1,
            parse_fn=AsyncMock(return_value=[]),
            browser_manager=AsyncMock(),
            shutdown_event=shutdown_event,
        )

    assert shutdown_event.is_set()
    set_scanning_mock.assert_awaited_once_with(False)
    broadcast_mock.assert_awaited_once()
    assert "Observer отключён" in broadcast_mock.await_args.kwargs["text"]
    assert any(
        call.kwargs.get("status") == "PAUSED" for call in update_status_mock.await_args_list
    )


# Проверяем что NOT_DELIVERING обрабатывается как уже выключенное объявление и не идёт в rule-evaluation.
@pytest.mark.asyncio
async def test_observer_loop_skips_rule_evaluation_for_not_delivering_rows():
    """Observer не должен повторно оценивать объявления со статусом NOT_DELIVERING."""
    from apps.observer_worker.main import observer_loop

    shutdown_event = asyncio.Event()
    captured_snapshot_batch: list[dict] = []
    scanned_row = SimpleNamespace(
        fb_ad_id="ad_not_delivering",
        campaign_name="Campaign",
        adset_name="Adset",
        ad_name="Ad",
        delivery_status="NOT_DELIVERING",
        spend=Decimal("1.00"),
        budget=None,
        reach=0,
        impressions=0,
        clicks=0,
        cpc=None,
        ctr=None,
        outbound_clicks=0,
        outbound_ctr=None,
        landing_page_views=0,
        cost_per_result=None,
        cost_per_landing_page_view=None,
        cpm=None,
        frequency=None,
        leads=0,
        cost_per_lead=None,
        registrations=0,
        cost_per_registration=None,
        deposits=0,
    )

    async def capture_snapshot_batch(snapshot_batch):
        captured_snapshot_batch.extend(snapshot_batch)
        shutdown_event.set()

    with ExitStack() as stack:
        _patch_observer_loop_runtime(
            stack,
            scan_side_effect=AsyncMock(return_value=[scanned_row]),
        )
        stack.enter_context(
            patch(
                "apps.observer_worker.main.batch_save_snapshots",
                new=AsyncMock(side_effect=capture_snapshot_batch),
            )
        )
        evaluate_row_mock = stack.enter_context(
            patch(
                "apps.observer_worker.main.evaluate_row",
                side_effect=AssertionError("evaluate_row не должен вызываться"),
            )
        )

        await observer_loop(
            page=AsyncMock(),
            offers={},
            telegram_bot_token="",
            telegram_chat_id="",
            interval_seconds=1,
            parse_fn=AsyncMock(return_value=[]),
            browser_manager=AsyncMock(),
            shutdown_event=shutdown_event,
        )

    evaluate_row_mock.assert_not_called()
    assert len(captured_snapshot_batch) == 1
    assert captured_snapshot_batch[0]["fb_ad_id"] == "ad_not_delivering"
    assert captured_snapshot_batch[0]["delivery_status"] == "NOT_DELIVERING"


# Проверяем retry-логику при ошибке браузера без зависания на бесконечном цикле
@pytest.mark.asyncio
async def test_reconnect_on_browser_error():
    """При ошибке связи с браузером должна быть попытка переподключения."""
    from apps.observer_worker.main import observer_loop

    mock_page = AsyncMock()
    mock_browser_manager = AsyncMock()
    mock_browser_manager.get_page.return_value = mock_page
    shutdown_event = asyncio.Event()

    call_count = 0

    async def failing_refresh(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            raise ConnectionError("Потеряна связь с браузером")
        shutdown_event.set()
        return []

    with ExitStack() as stack:
        _patch_observer_loop_runtime(stack, scan_side_effect=failing_refresh)
        await observer_loop(
            page=mock_page,
            offers={},
            telegram_bot_token="",
            telegram_chat_id="",
            interval_seconds=1,
            parse_fn=AsyncMock(),
            browser_manager=mock_browser_manager,
            shutdown_event=shutdown_event,
        )

    # Должно быть 2 попытки переподключения (disconnect + connect)
    assert mock_browser_manager.disconnect.call_count == 2
    assert mock_browser_manager.connect.call_count == 2


# Проверяем что после MAX_RECONNECT_ATTEMPTS попыток воркер завершается детерминированно
@pytest.mark.asyncio
async def test_reconnect_max_attempts_exit():
    """После MAX_RECONNECT_ATTEMPTS ошибок подряд воркер должен завершиться."""
    from apps.observer_worker.main import MAX_RECONNECT_ATTEMPTS, observer_loop

    mock_page = AsyncMock()
    mock_browser_manager = AsyncMock()
    mock_browser_manager.get_page.return_value = mock_page

    # Всегда падаем с ошибкой связи
    async def always_fail(**kwargs):
        raise ConnectionError("Потеряна связь с браузером")

    with ExitStack() as stack:
        _patch_observer_loop_runtime(stack, scan_side_effect=always_fail)
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


# Проверяем что успешный цикл сбрасывает backoff перед следующей browser-ошибкой
@pytest.mark.asyncio
async def test_reconnect_counter_resets_on_success():
    """Успешный цикл должен сбрасывать счётчик ошибок браузера."""
    from apps.observer_worker.main import observer_loop

    mock_page = AsyncMock()
    mock_browser_manager = AsyncMock()
    mock_browser_manager.get_page.return_value = mock_page
    shutdown_event = asyncio.Event()

    call_count = 0

    async def mixed_refresh(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise ConnectionError("Потеряна связь")
        if call_count == 2:
            return []
        shutdown_event.set()
        raise ConnectionError("Потеряна связь повторно")

    with ExitStack() as stack:
        sleep_mock = _patch_observer_loop_runtime(stack, scan_side_effect=mixed_refresh)
        await observer_loop(
            page=mock_page,
            offers={},
            telegram_bot_token="",
            telegram_chat_id="",
            interval_seconds=1,
            parse_fn=AsyncMock(),
            browser_manager=mock_browser_manager,
            shutdown_event=shutdown_event,
        )

    reconnect_delays = [
        call.args[0]
        for call in sleep_mock.await_args_list
        if call.args and call.args[0] in (10, 20, 30)
    ]

    assert reconnect_delays[:2] == [10, 10]
    assert mock_browser_manager.disconnect.call_count == 2
