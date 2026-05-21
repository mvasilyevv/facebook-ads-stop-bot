# -*- coding: utf-8 -*-
"""Unit-тесты улучшений observer worker: jitter, batch upsert, FSM из БД, reconnect."""

from __future__ import annotations

import asyncio
import uuid
from contextlib import ExitStack, asynccontextmanager
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


def _plain_result(rows):
    """Создаёт мок результата SQLAlchemy для result.all() (без scalars)."""
    result = MagicMock()
    result.all.return_value = rows
    result.scalars.return_value.all.return_value = rows
    return result


def _make_snapshot_ns(
    *,
    fb_ad_id: str,
    ad_id: str = "ad-uuid-1",
    campaign_name: str = "Campaign",
    adset_name: str = "Adset",
    ad_name: str = "Тестовое объявление",
    offer_id: object = None,
    offer_code: str | None = None,
    **kwargs,
) -> SimpleNamespace:
    """Создаёт мок снэпшота с нормализованной цепочкой fb_ad → adset → campaign."""
    campaign_ns = SimpleNamespace(
        campaign_name=campaign_name,
        offer_id=offer_id,
        offer_code=offer_code,
    )
    adset_ns = SimpleNamespace(
        adset_name=adset_name,
        campaign=campaign_ns,
    )
    fb_ad_ns = SimpleNamespace(
        ad_name=ad_name,
        adset=adset_ns,
    )
    return SimpleNamespace(
        fb_ad_id=fb_ad_id,
        ad_id=ad_id,
        fb_ad=fb_ad_ns,
        **kwargs,
    )


def _telegram_destination(
    *,
    chat_id: str = "chat-1",
) -> TelegramDestination:
    """Собирает тестовый destination для доставки Telegram-алертов."""
    return TelegramDestination(
        chat_id=chat_id,
        telegram_user_id="42",
        role="owner",
        username="owner",
        first_name="Иван",
        is_primary=True,
    )


# --- Тесты jitter (задача 1.5) ---


# Проверяем что compute_jitter возвращает значение в диапазоне 50-150% от interval
def test_compute_jitter_range():
    """Задержка должна быть в пределах 50-150% от interval_seconds."""
    from apps.observer_worker.main import compute_jitter  # compute_jitter остаётся в main

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


# Проверяем что batch_save_snapshots вызывает нормализованные upsert-ы и коммитит
@pytest.mark.asyncio
async def test_batch_save_snapshots_single_query():
    """Batch upsert должен пройти через нормализованные таблицы и сделать один commit."""
    import uuid as _uuid

    from core.observer.scan_guard import ZeroScanGuard
    from core.observer.snapshot_writer import batch_save_snapshots

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_factory = MagicMock(return_value=mock_session)

    fake_ad_ids = {f"ad_{i}": _uuid.uuid4() for i in range(50)}
    upsert_campaigns_mock = AsyncMock(return_value={"campaign": _uuid.uuid4()})
    upsert_adsets_mock = AsyncMock(return_value={("campaign", "adset"): _uuid.uuid4()})
    upsert_ads_mock = AsyncMock(return_value=fake_ad_ids)
    save_deltas_mock = AsyncMock(return_value=0)
    upsert_snapshots_mock = AsyncMock()

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

    scan_guard = ZeroScanGuard()
    with (
        patch("core.observer.snapshot_writer.get_session_factory", return_value=mock_factory),
        patch("core.observer.snapshot_writer._maybe_rollover_cabinet_day", new=AsyncMock()),
        patch("core.observer.snapshot_writer._upsert_fb_campaigns", new=upsert_campaigns_mock),
        patch("core.observer.snapshot_writer._upsert_fb_adsets", new=upsert_adsets_mock),
        patch("core.observer.snapshot_writer._upsert_fb_ads", new=upsert_ads_mock),
        patch("core.observer.snapshot_writer._save_metric_deltas", new=save_deltas_mock),
        patch("core.observer.snapshot_writer._upsert_ad_snapshots", new=upsert_snapshots_mock),
    ):
        await batch_save_snapshots(snapshot_data, scan_guard, current_scan_id=1)

    # Все нормализованные шаги вызваны
    upsert_campaigns_mock.assert_awaited_once()
    upsert_adsets_mock.assert_awaited_once()
    upsert_ads_mock.assert_awaited_once()
    save_deltas_mock.assert_awaited_once()
    upsert_snapshots_mock.assert_awaited_once()
    # И один commit
    assert mock_session.commit.call_count == 1


# Проверяем что пустые campaign/adset пропускаются при нормализованном upsert
@pytest.mark.asyncio
async def test_batch_save_snapshots_preserves_identity_names_on_empty_update():
    """При пустых campaign/adset нормализованные таблицы не должны создавать пустые записи."""
    from core.observer.scan_guard import ZeroScanGuard
    from core.observer.snapshot_writer import batch_save_snapshots

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    # При пустых campaign_name/adset_name _upsert_fb_campaigns вернёт пустой маппинг,
    # и далее весь pipeline пропустит эти записи
    mock_session.execute = AsyncMock(
        side_effect=[
            _scalars_result([]),  # metric deltas: select current snapshots (пустой)
        ]
    )

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

    scan_guard = ZeroScanGuard()
    with (
        patch(
            "core.observer.snapshot_writer.get_session_factory",
            return_value=mock_factory,
        ),
        patch(
            "core.observer.snapshot_writer._maybe_rollover_cabinet_day",
            new=AsyncMock(),
        ),
    ):
        await batch_save_snapshots(snapshot_data, scan_guard, current_scan_id=1)

    # При пустых campaign_name кампании не создаются — pipeline не дойдёт до upsert snapshots
    # Commit всё равно вызывается
    assert mock_session.commit.call_count == 1


# Проверяем что первый полный zero-scan не затирает живой батч без повторного подтверждения.
@pytest.mark.asyncio
async def test_batch_save_snapshots_requires_confirmed_zero_scan_before_persist():
    """Подозрительный zero-scan должен пропускаться один цикл и применяться только после повтора."""
    import uuid as _uuid

    from core.observer.scan_guard import ZeroScanGuard
    from core.observer.snapshot_writer import batch_save_snapshots

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    fake_campaign_id = _uuid.uuid4()
    fake_adset_id = _uuid.uuid4()
    fake_ad_id = _uuid.uuid4()

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

    scan_guard = ZeroScanGuard()
    with (
        patch(
            "core.observer.snapshot_writer.get_session_factory",
            return_value=mock_factory,
        ),
        patch(
            "core.observer.snapshot_writer._maybe_rollover_cabinet_day",
            new=AsyncMock(),
        ) as rollover_mock,
        patch(
            "core.observer.snapshot_writer._upsert_fb_campaigns",
            new=AsyncMock(return_value={"campaign": fake_campaign_id}),
        ),
        patch(
            "core.observer.snapshot_writer._upsert_fb_adsets",
            new=AsyncMock(return_value={("campaign", "adset"): fake_adset_id}),
        ),
        patch(
            "core.observer.snapshot_writer._upsert_fb_ads",
            new=AsyncMock(return_value={"ad_1": fake_ad_id}),
        ),
        patch(
            "core.observer.snapshot_writer._save_metric_deltas",
            new=AsyncMock(return_value=0),
        ),
        patch(
            "core.observer.snapshot_writer._upsert_ad_snapshots",
            new=AsyncMock(),
        ),
    ):
        # Первый вызов — zero-scan пропускается scan_guard
        await batch_save_snapshots(snapshot_data, scan_guard, current_scan_id=1)
        # Второй вызов — подтверждённый zero-scan проходит
        await batch_save_snapshots(snapshot_data, scan_guard, current_scan_id=2)

    # Первый zero-scan пропущен, второй проходит — один commit
    assert mock_session.commit.call_count == 1
    rollover_mock.assert_awaited_once()


# Проверяем, что подтверждённый zero-scan не начинает бесконечно пропускаться через цикл.
@pytest.mark.asyncio
async def test_batch_save_snapshots_accepts_zero_scan_after_confirmation():
    """После подтверждения zero-scan следующие нулевые срезы должны сохраняться сразу."""
    import uuid as _uuid

    from core.observer.scan_guard import ZeroScanGuard
    from core.observer.snapshot_writer import batch_save_snapshots

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    fake_campaign_id = _uuid.uuid4()
    fake_adset_id = _uuid.uuid4()
    fake_ad_id = _uuid.uuid4()
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

    scan_guard = ZeroScanGuard()
    with (
        patch("core.observer.snapshot_writer.get_session_factory", return_value=mock_factory),
        patch(
            "core.observer.snapshot_writer._maybe_rollover_cabinet_day",
            new=AsyncMock(),
        ),
        patch(
            "core.observer.snapshot_writer._upsert_fb_campaigns",
            new=AsyncMock(return_value={"campaign": fake_campaign_id}),
        ),
        patch(
            "core.observer.snapshot_writer._upsert_fb_adsets",
            new=AsyncMock(return_value={("campaign", "adset"): fake_adset_id}),
        ),
        patch(
            "core.observer.snapshot_writer._upsert_fb_ads",
            new=AsyncMock(return_value={"ad_1": fake_ad_id}),
        ),
        patch("core.observer.snapshot_writer._save_metric_deltas", new=AsyncMock(return_value=0)),
        patch("core.observer.snapshot_writer._upsert_ad_snapshots", new=AsyncMock()),
    ):
        first_saved = await batch_save_snapshots(snapshot_data, scan_guard, current_scan_id=1)
        second_saved = await batch_save_snapshots(snapshot_data, scan_guard, current_scan_id=2)
        third_saved = await batch_save_snapshots(snapshot_data, scan_guard, current_scan_id=3)

    assert first_saved is False
    assert second_saved is True
    assert third_saved is True
    assert mock_session.commit.call_count == 2


# Проверяем, что регресс накопительных метрик считается подозрительным.
def test_has_cumulative_metric_regression_detects_daily_metric_drop():
    """Новый срез с меньшим расходом не должен затирать дневные накопительные метрики."""
    from core.observer.snapshot_writer import _has_cumulative_metric_regression

    old_snapshot = SimpleNamespace(
        spend=Decimal("12.34"),
        clicks=20,
        leads=3,
        registrations=1,
        deposits=0,
        outbound_clicks=10,
        landing_page_views=4,
    )
    new_row = {
        "spend": Decimal("0.02"),
        "clicks": 0,
        "leads": 0,
        "registrations": 0,
        "deposits": 0,
        "outbound_clicks": 0,
        "landing_page_views": 0,
    }

    assert _has_cumulative_metric_regression(old_snapshot, new_row) is True


# Проверяем, что история метрик не загрязняется неполным срезом с откатом.
@pytest.mark.asyncio
async def test_save_metric_deltas_skips_cumulative_metric_regression():
    """Регресс дневных метрик должен быть пропущен до записи в ad_metric_history."""
    from core.observer.snapshot_writer import _save_metric_deltas

    ad_id = uuid.uuid4()
    old_snapshot = SimpleNamespace(
        fb_ad_id="ad-regression",
        spend=Decimal("8.00"),
        clicks=12,
        leads=2,
        registrations=1,
        deposits=0,
        outbound_clicks=7,
        landing_page_views=3,
    )
    session = AsyncMock()
    session.execute = AsyncMock(return_value=_scalars_result([old_snapshot]))

    inserted = await _save_metric_deltas(
        session,
        [
            {
                "fb_ad_id": "ad-regression",
                "spend": Decimal("0.02"),
                "clicks": 0,
                "leads": 0,
                "registrations": 0,
                "deposits": 0,
                "outbound_clicks": 0,
                "landing_page_views": 0,
            }
        ],
        {"ad-regression": ad_id},
    )

    assert inserted == 0
    assert session.execute.await_count == 1


# Проверяем что резкое проседание количества строк не затирает live-срез без подтверждения.
@pytest.mark.asyncio
async def test_batch_save_snapshots_requires_confirmed_partial_batch_before_persist():
    """Первый подозрительно неполный non-zero батч должен пропускаться до повторного подтверждения."""
    import uuid as _uuid

    from core.observer.scan_guard import ZeroScanGuard
    from core.observer.snapshot_writer import batch_save_snapshots

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

    scan_guard = ZeroScanGuard()
    with (
        patch(
            "core.observer.snapshot_writer.get_session_factory",
            return_value=mock_factory,
        ),
        patch(
            "core.observer.snapshot_writer._maybe_rollover_cabinet_day",
            new=AsyncMock(),
        ),
        patch(
            "core.observer.snapshot_writer._upsert_fb_campaigns",
            new=AsyncMock(return_value={"campaign": _uuid.uuid4()}),
        ),
        patch(
            "core.observer.snapshot_writer._upsert_fb_adsets",
            new=AsyncMock(return_value={("campaign", "adset"): _uuid.uuid4()}),
        ),
        patch(
            "core.observer.snapshot_writer._upsert_fb_ads",
            new=AsyncMock(return_value={f"ad_{i}": _uuid.uuid4() for i in range(30)}),
        ),
        patch(
            "core.observer.snapshot_writer._save_metric_deltas",
            new=AsyncMock(return_value=0),
        ),
        patch(
            "core.observer.snapshot_writer._upsert_ad_snapshots",
            new=AsyncMock(),
        ),
    ):
        await batch_save_snapshots(full_snapshot_data, scan_guard, current_scan_id=1)
        await batch_save_snapshots(partial_snapshot_data, scan_guard, current_scan_id=2)
        await batch_save_snapshots(partial_snapshot_data, scan_guard, current_scan_id=3)

    # Первый полный батч + подтверждённый partial (третий вызов) = 2 коммита
    assert mock_session.commit.call_count == 2


# Проверяем что быстрый STOP может сохранить одну строку, не ломая защиту от частичных батчей.
@pytest.mark.asyncio
async def test_batch_save_snapshots_bypasses_guard_for_fast_stop_partial_batch():
    """Быстрый стоп сохраняет точечный снэпшот сразу, но не меняет базовый размер scan_guard."""
    import uuid as _uuid

    from core.observer.scan_guard import ZeroScanGuard
    from core.observer.snapshot_writer import batch_save_snapshots

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
    fast_stop_snapshot = [build_snapshot(0) | {"current_stage": AlertStage.STOP}]

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_factory = MagicMock(return_value=mock_session)

    scan_guard = ZeroScanGuard()
    with (
        patch(
            "core.observer.snapshot_writer.get_session_factory",
            return_value=mock_factory,
        ),
        patch(
            "core.observer.snapshot_writer._maybe_rollover_cabinet_day",
            new=AsyncMock(),
        ),
        patch(
            "core.observer.snapshot_writer._upsert_fb_campaigns",
            new=AsyncMock(return_value={"campaign": _uuid.uuid4()}),
        ),
        patch(
            "core.observer.snapshot_writer._upsert_fb_adsets",
            new=AsyncMock(return_value={("campaign", "adset"): _uuid.uuid4()}),
        ),
        patch(
            "core.observer.snapshot_writer._upsert_fb_ads",
            new=AsyncMock(return_value={f"ad_{i}": _uuid.uuid4() for i in range(30)}),
        ),
        patch(
            "core.observer.snapshot_writer._save_metric_deltas",
            new=AsyncMock(return_value=0),
        ),
        patch(
            "core.observer.snapshot_writer._upsert_ad_snapshots",
            new=AsyncMock(),
        ),
    ):
        await batch_save_snapshots(full_snapshot_data, scan_guard, current_scan_id=1)
        await batch_save_snapshots(
            fast_stop_snapshot,
            scan_guard,
            allow_cabinet_rollover=False,
            bypass_scan_guard=True,
            current_scan_id=2,
        )
        await batch_save_snapshots(fast_stop_snapshot, scan_guard, current_scan_id=3)

    # Полный срез и быстрый стоп сохранены, обычный частичный батч после этого всё ещё блокируется.
    assert mock_session.commit.call_count == 2


# Проверяем что CLAIMED ждёт подтверждения OFF, а DISABLED снимается только при новой активации
def test_reopen_reactivated_alert_state_keeps_claimed_and_resets_disabled():
    """CLAIMED не должен сбрасываться до подтверждения OFF следующим сканом."""
    from core.observer.state_machine import reopen_reactivated_alert_state

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
    from core.observer.disable_reconciler import reconcile_disable_incidents_after_scan

    now = datetime.now(UTC)
    snapshot = _make_snapshot_ns(
        fb_ad_id="ad_001",
        ad_name="Тестовое объявление",
        offer_code="DRC",
        delivery_status="UNKNOWN",
        alert_state=AlertState.CLAIMED,
        current_stage=AlertStage.STOP,
        open_state_token="incident-001",
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
        last_observed_at=now,
    )
    latest_succeeded = SimpleNamespace(completed_at=now - timedelta(seconds=10))
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.execute = AsyncMock(return_value=_scalars_result([snapshot]))
    mock_session.scalar = AsyncMock(side_effect=[now, 0, latest_succeeded])
    mock_factory = MagicMock(return_value=mock_session)

    with patch("core.observer.disable_reconciler.get_session_factory", return_value=mock_factory):
        alerts = await reconcile_disable_incidents_after_scan()

    assert alerts == []
    mock_session.commit.assert_not_awaited()


# Проверяем что короткий grace после SUCCEEDED не держит активный STOP-инцидент слишком долго.
@pytest.mark.asyncio
async def test_reconcile_disable_incidents_after_scan_retries_after_success_grace_expired():
    """Если после успешной попытки свежий STOP-скан всё ещё активен, нужен повторный disable."""
    from core.observer.disable_reconciler import reconcile_disable_incidents_after_scan

    now = datetime.now(UTC)
    snapshot = _make_snapshot_ns(
        fb_ad_id="ad_001_retry",
        ad_name="Тестовое объявление retry",
        offer_code="DRC",
        delivery_status="ACTIVE",
        alert_state=AlertState.CLAIMED,
        current_stage=AlertStage.STOP,
        open_state_token="incident-retry",
        stop_rule_codes=["cpr_stop"],
        warning_rule_codes=[],
        early_signal_rule_codes=[],
        spend=Decimal("1.70"),
        clicks=28,
        cpc=Decimal("0.0600"),
        outbound_clicks=11,
        outbound_ctr=Decimal("0.8600"),
        landing_page_views=5,
        cost_per_landing_page_view=Decimal("0.1900"),
        cpm=Decimal("0.7300"),
        frequency=Decimal("1.2000"),
        leads=7,
        cost_per_lead=Decimal("0.2400"),
        registrations=0,
        cost_per_registration=None,
        deposits=0,
        last_observed_at=now,
    )
    latest_succeeded = SimpleNamespace(completed_at=now - timedelta(minutes=3), last_error=None)
    latest_task = SimpleNamespace(last_error=None)
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.execute = AsyncMock(return_value=_scalars_result([snapshot]))
    mock_session.scalar = AsyncMock(side_effect=[now, 0, latest_succeeded, 1, latest_task])
    mock_factory = MagicMock(return_value=mock_session)

    with (
        patch("core.observer.disable_reconciler.get_session_factory", return_value=mock_factory),
        patch(
            "core.observer.disable_reconciler._create_auto_disable_task_for_snapshot",
            new=AsyncMock(return_value=True),
        ) as create_attempt,
    ):
        alerts = await reconcile_disable_incidents_after_scan()

    assert alerts == []
    create_attempt.assert_awaited_once()
    mock_session.commit.assert_awaited_once()


# Проверяем что исчерпанный grace создаёт тихий follow-up disable без нового STOP-алерта.
@pytest.mark.asyncio
async def test_reconcile_disable_incidents_after_scan_creates_follow_up_attempt():
    """Если OFF не подтвердился, observer должен создать новую auto-disable попытку в том же incident."""
    from core.observer.disable_reconciler import reconcile_disable_incidents_after_scan

    snapshot = _make_snapshot_ns(
        fb_ad_id="ad_002",
        ad_name="Тестовое объявление 2",
        offer_code="DRC",
        delivery_status="UNKNOWN",
        alert_state=AlertState.CLAIMED,
        current_stage=AlertStage.STOP,
        open_state_token="incident-002",
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
    mock_session.scalar = AsyncMock(side_effect=[datetime.now(UTC), 0, None, 1, latest_task])
    mock_factory = MagicMock(return_value=mock_session)

    with (
        patch("core.observer.disable_reconciler.get_session_factory", return_value=mock_factory),
        patch(
            "core.observer.disable_reconciler._create_auto_disable_task_for_snapshot",
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
    from core.observer.disable_reconciler import reconcile_disable_incidents_after_scan

    snapshot = _make_snapshot_ns(
        fb_ad_id="ad_003",
        ad_name="Тестовое объявление 3",
        offer_code="DRC",
        delivery_status="UNKNOWN",
        alert_state=AlertState.CLAIMED,
        current_stage=AlertStage.STOP,
        open_state_token="incident-003",
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
    mock_session.scalar = AsyncMock(side_effect=[datetime.now(UTC), 0, None, 4, latest_task])
    mock_factory = MagicMock(return_value=mock_session)

    with (
        patch("core.observer.disable_reconciler.get_session_factory", return_value=mock_factory),
        patch(
            "core.observer.disable_reconciler._create_auto_disable_task_for_snapshot",
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
    from core.observer.service import _compose_reason_text

    assert (
        _compose_reason_text("Основная причина.", "CPM выше медианы.")
        == "Основная причина. CPM выше медианы."
    )
    assert _compose_reason_text("Только причина.", None) == "Только причина."
    assert _compose_reason_text(None, "Только диагностика.") == "Только диагностика."


# Проверяем, что snooze не подавляет STOP-напоминание.
@pytest.mark.asyncio
async def test_collect_reminder_alerts_keeps_stop_even_if_snoozed():
    """STOP-напоминание должно пройти даже при активном snoozed_until."""
    from core.observer.db_queries import collect_reminder_alerts

    now = datetime.now(UTC)
    snap = _make_snapshot_ns(
        fb_ad_id="ad_stop",
        ad_name="STOP объявление",
        offer_code="DRC",
        alert_state=AlertState.STOP_SENT,
        snoozed_until=now + timedelta(hours=2),
        open_state_token="token_stop",
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
    snap.ad_id = 303
    fb_ad_ns = SimpleNamespace(ad_name="STOP объявление", adset=None)
    snap.fb_ad = fb_ad_ns

    candidates_result = MagicMock()
    candidates_result.scalars.return_value.all.return_value = [snap]

    last_event_at_row = SimpleNamespace(ad_id=303, max_at=now - timedelta(minutes=20))
    last_event_at_result = MagicMock()
    last_event_at_result.__iter__ = MagicMock(return_value=iter([last_event_at_row]))

    latest_events_result = MagicMock()
    latest_events_result.scalars.return_value.all.return_value = [
        SimpleNamespace(
            ad_id=303,
            reason_title="Стоп без подтверждения OFF",
            reason_text="Нужно проверить отключение вручную.",
            metrics_json={"rule_summaries": ["CPC выше стопа"]},
        )
    ]

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.execute = AsyncMock(
        side_effect=[candidates_result, last_event_at_result, latest_events_result]
    )
    mock_session.scalar = AsyncMock(return_value=now)
    mock_factory = MagicMock(return_value=mock_session)

    with patch("core.observer.db_queries.get_session_factory", return_value=mock_factory):
        reminders = await collect_reminder_alerts()

    assert len(reminders) == 1
    assert reminders[0].stage == AlertStage.STOP


# Проверяем что архивные объявления не попадают в повторные WARNING-напоминания
@pytest.mark.asyncio
async def test_collect_reminder_alerts_skips_archived_snapshots():
    """Напоминания должны отправляться только по объявлениям из актуальной скан-сессии."""
    from core.observer.db_queries import collect_reminder_alerts

    now = datetime.now(UTC)
    archived_snap = _make_snapshot_ns(
        fb_ad_id="ad_archived",
        ad_name="Архивное объявление",
        offer_code="DRC",
        alert_state=AlertState.WARNING_SENT,
        snoozed_until=None,
        open_state_token="token_archived",
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
        "core.observer.db_queries.get_session_factory",
        return_value=mock_factory,
    ):
        reminders = await collect_reminder_alerts()

    assert reminders == []
    mock_session.execute.assert_awaited_once()
    mock_session.scalar.assert_awaited_once()


# Проверяем что авто-стоп не ставит disable-задачу для архивного объявления
@pytest.mark.asyncio
async def test_auto_create_disable_tasks_skips_archived_snapshot():
    """Авто-стоп должен пропускать snapshot, который уже выпал из актуального окна."""
    from core.observer.disable_reconciler import auto_create_disable_tasks

    now = datetime.now(UTC)
    alert = SimpleNamespace(
        fb_ad_id="ad_archived",
        ad_name="Архивный стоп",
        snapshot_id="token-stop",
    )
    snapshot = SimpleNamespace(
        id="snapshot-1",
        ad_id="ad-uuid-archived",
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
        "core.observer.disable_reconciler.get_session_factory",
        return_value=mock_factory,
    ):
        await auto_create_disable_tasks([alert])

    mock_session.execute.assert_not_awaited()
    mock_session.commit.assert_not_awaited()


# Проверяем что при сбое Telegram алерт не сохраняется как доставленный
@pytest.mark.asyncio
async def test_send_alerts_to_telegram_skips_persist_on_failure():
    """Если Telegram не принял сообщение, AlertEvent сохранять нельзя."""
    from apps.observer_worker.main import _send_alerts_to_telegram  # Остаётся в main

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

    mock_empty_result = MagicMock()
    mock_empty_result.scalars = MagicMock(return_value=mock_empty_result)
    mock_empty_result.all = MagicMock(return_value=[])
    mock_session.execute = AsyncMock(return_value=mock_empty_result)

    mock_factory = MagicMock(return_value=mock_session)

    with (
        patch("core.observer.disable_reconciler.get_session_factory", return_value=mock_factory),
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
    from apps.observer_worker.main import _send_alerts_to_telegram  # Остаётся в main

    destination = _telegram_destination(
        chat_id="chat-1",
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
        fb_ad_id="ad_same_incident",
    )
    fb_ad_obj = SimpleNamespace(id="ad-uuid-777", fb_ad_id="ad_same_incident")
    existing_stage_event = SimpleNamespace(
        snapshot_id=None,
        ad_id=None,
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
    mock_session.scalar = AsyncMock(return_value=existing_stage_event)

    # Мокаем execute для batch-запросов snapshot/fb_ad
    mock_scalars_result = MagicMock()
    mock_scalars_result.scalars = MagicMock(return_value=mock_scalars_result)
    mock_scalars_result.all = MagicMock(return_value=[snapshot])
    mock_fb_ad_result = MagicMock()
    mock_fb_ad_result.scalars = MagicMock(return_value=mock_fb_ad_result)
    mock_fb_ad_result.all = MagicMock(return_value=[fb_ad_obj])

    call_count = 0

    async def mock_execute(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return mock_scalars_result
        return mock_fb_ad_result

    mock_session.execute = mock_execute

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
    assert fake_client.edit_message.await_args.kwargs["message_thread_id"] is None
    mock_session.add.assert_not_called()
    assert existing_stage_event.reason_title == "Нужна ручная проверка отключения"
    assert existing_stage_event.telegram_message_id == 321
    upsert_ref.assert_awaited_once()


# Проверяем что пустой список не вызывает запросов
@pytest.mark.asyncio
async def test_batch_save_snapshots_empty_list():
    """При пустом списке не должно быть обращений к БД."""
    from core.observer.scan_guard import ZeroScanGuard
    from core.observer.snapshot_writer import batch_save_snapshots

    mock_factory = MagicMock()
    scan_guard = ZeroScanGuard()

    with patch(
        "core.observer.snapshot_writer.get_session_factory",
        return_value=mock_factory,
    ):
        await batch_save_snapshots([], scan_guard)

    # Фабрика не должна вызываться при пустом списке
    mock_factory.assert_not_called()


# --- Тесты FSM загрузки из БД (задача 2.3) ---


# Проверяем что ad_states заполняется из БД при старте
@pytest.mark.asyncio
async def test_load_ad_states_from_db():
    """FSM-состояния должны загружаться из AdSnapshot при старте."""
    from core.observer.db_queries import load_ad_states_from_db

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
        "core.observer.db_queries.get_session_factory",
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
    from core.observer.db_queries import load_ad_states_from_db

    mock_result = MagicMock()
    mock_result.all.return_value = []

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.execute = AsyncMock(return_value=mock_result)

    mock_factory = MagicMock(return_value=mock_session)

    with patch(
        "core.observer.db_queries.get_session_factory",
        return_value=mock_factory,
    ):
        states = await load_ad_states_from_db()

    assert states == {}


# Проверяем что реальный OFF сохраняет DISABLED для ранее выключавшихся объявлений
def test_resolve_off_alert_state_keeps_disabled_for_claimed_and_disabled():
    """При delivery=OFF состояния CLAIMED и DISABLED должны оставаться терминальным DISABLED."""
    from core.observer.state_machine import resolve_off_alert_state

    assert resolve_off_alert_state(AlertState.CLAIMED) == AlertState.DISABLED
    assert resolve_off_alert_state(AlertState.DISABLED) == AlertState.DISABLED
    assert resolve_off_alert_state(AlertState.NORMAL) == AlertState.NORMAL


# Проверяем что Vision-настройки для запуска берутся из БД
@pytest.mark.asyncio
async def test_load_vision_settings_for_runtime_prefers_db():
    """Если в БД есть Vision-настройки, они должны перекрывать fallback env."""
    from core.observer.db_queries import load_vision_settings_for_runtime

    with patch(
        "core.observer.db_queries.load_vision_settings_from_db",
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
    from core.observer.db_queries import load_vision_settings_for_runtime

    with patch(
        "core.observer.db_queries.load_vision_settings_from_db",
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
    from core.domain import DisableTaskStatus
    from core.observer.db_queries import get_disable_queue_pause_reason

    now = datetime.now(UTC)
    mock_result = MagicMock()
    mock_result.all.return_value = [
        (DisableTaskStatus.PENDING, None, now, now, now),
        (DisableTaskStatus.RETRYING, None, now, now, now),
    ]

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.scalar = AsyncMock(return_value=now)
    mock_session.execute = AsyncMock(return_value=mock_result)

    mock_factory = MagicMock(return_value=mock_session)

    with patch(
        "core.observer.db_queries.get_session_factory",
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
    from core.observer.db_queries import get_disable_queue_pause_reason

    mock_result = MagicMock()
    mock_result.all.return_value = []

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.scalar = AsyncMock(return_value=datetime.now(UTC))
    mock_session.execute = AsyncMock(return_value=mock_result)

    mock_factory = MagicMock(return_value=mock_session)

    with patch(
        "core.observer.db_queries.get_session_factory",
        return_value=mock_factory,
    ):
        reason = await get_disable_queue_pause_reason()

    assert reason is None


# Проверяем что отложенный retry не должен ставить observer на паузу раньше времени
@pytest.mark.asyncio
async def test_get_disable_queue_pause_reason_ignores_future_retry_only_queue():
    """Если в очереди остались только будущие RETRYING-задачи, сканирование не должно стопориться."""
    from core.domain import DisableTaskStatus
    from core.observer.db_queries import get_disable_queue_pause_reason

    now = datetime.now(UTC)
    mock_result = MagicMock()
    mock_result.all.return_value = [
        (
            DisableTaskStatus.RETRYING,
            now + timedelta(minutes=3),
            now,
            now,
            now,
        ),
    ]

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.scalar = AsyncMock(return_value=now)
    mock_session.execute = AsyncMock(return_value=mock_result)

    mock_factory = MagicMock(return_value=mock_session)

    with patch(
        "core.observer.db_queries.get_session_factory",
        return_value=mock_factory,
    ):
        reason = await get_disable_queue_pause_reason()

    assert reason is None


# Проверяем что устаревшие disable-задачи не держат observer в вечной паузе
@pytest.mark.asyncio
async def test_get_disable_queue_pause_reason_ignores_stale_snapshot_queue():
    """Если snapshot устарел, очередь должна считаться неактуальной и не блокировать новый scan."""
    from core.domain import DisableTaskStatus
    from core.observer.db_queries import get_disable_queue_pause_reason

    now = datetime.now(UTC)
    stale_snapshot_time = now - timedelta(hours=2)
    mock_result = MagicMock()
    mock_result.all.return_value = [
        (
            DisableTaskStatus.RUNNING,
            None,
            now,
            now,
            stale_snapshot_time,
        ),
        (
            DisableTaskStatus.PENDING,
            None,
            now,
            now,
            stale_snapshot_time,
        ),
    ]

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.scalar = AsyncMock(return_value=stale_snapshot_time)
    mock_session.execute = AsyncMock(return_value=mock_result)

    mock_factory = MagicMock(return_value=mock_session)

    with patch(
        "core.observer.db_queries.get_session_factory",
        return_value=mock_factory,
    ):
        reason = await get_disable_queue_pause_reason()

    assert reason is None


# Проверяем что активная очередь включения тоже ставит observer на паузу
@pytest.mark.asyncio
async def test_get_enable_queue_pause_reason_reports_active_queue():
    """Если есть PENDING и RUNNING enable-задачи, observer должен видеть причину для паузы."""
    from core.domain import EnableTaskStatus
    from core.observer.db_queries import get_enable_queue_pause_reason

    now = datetime.now(UTC)
    mock_result = MagicMock()
    mock_result.all.return_value = [
        (EnableTaskStatus.PENDING, None, now, now, now, now),
        (EnableTaskStatus.RUNNING, None, now, now, now, now),
    ]

    observer_settings = MagicMock(cabinet_day_started_at=now - timedelta(minutes=5))
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.scalar = AsyncMock(return_value=now)
    mock_session.execute = AsyncMock(return_value=mock_result)

    mock_factory = MagicMock(return_value=mock_session)

    with (
        patch(
            "core.observer.db_queries.get_session_factory",
            return_value=mock_factory,
        ),
        patch(
            "core.observer.db_queries.get_observer_settings",
            new=AsyncMock(return_value=observer_settings),
        ),
    ):
        reason = await get_enable_queue_pause_reason()

    assert reason is not None
    assert "ожидают: 1" in reason
    assert "выполняются: 1" in reason


# Проверяем что отложенный retry включения не должен ставить observer на паузу раньше времени
@pytest.mark.asyncio
async def test_get_enable_queue_pause_reason_ignores_future_retry_only_queue():
    """Если в очереди остались только будущие RETRYING-enable-задачи, сканирование не должно стопориться."""
    from core.domain import EnableTaskStatus
    from core.observer.db_queries import get_enable_queue_pause_reason

    now = datetime.now(UTC)
    mock_result = MagicMock()
    mock_result.all.return_value = [
        (
            EnableTaskStatus.RETRYING,
            now + timedelta(minutes=3),
            now,
            now,
            now,
            now,
        ),
    ]

    observer_settings = MagicMock(cabinet_day_started_at=now - timedelta(minutes=5))
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.scalar = AsyncMock(return_value=now)
    mock_session.execute = AsyncMock(return_value=mock_result)

    mock_factory = MagicMock(return_value=mock_session)

    with (
        patch(
            "core.observer.db_queries.get_session_factory",
            return_value=mock_factory,
        ),
        patch(
            "core.observer.db_queries.get_observer_settings",
            new=AsyncMock(return_value=observer_settings),
        ),
    ):
        reason = await get_enable_queue_pause_reason()

    assert reason is None


# Проверяем что устаревшие enable-задачи не держат observer в вечной паузе
@pytest.mark.asyncio
async def test_get_enable_queue_pause_reason_ignores_stale_snapshot_queue():
    """Если live batch уже устарел, очередь включения не должна блокировать новый scan."""
    from core.domain import EnableTaskStatus
    from core.observer.db_queries import get_enable_queue_pause_reason

    now = datetime.now(UTC)
    stale_snapshot_time = now - timedelta(hours=2)
    mock_result = MagicMock()
    mock_result.all.return_value = [
        (
            EnableTaskStatus.RUNNING,
            None,
            now,
            now,
            stale_snapshot_time,
            stale_snapshot_time,
        ),
        (
            EnableTaskStatus.PENDING,
            None,
            now,
            now,
            stale_snapshot_time,
            stale_snapshot_time,
        ),
    ]

    observer_settings = MagicMock(cabinet_day_started_at=now - timedelta(minutes=5))
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.scalar = AsyncMock(return_value=stale_snapshot_time)
    mock_session.execute = AsyncMock(return_value=mock_result)

    mock_factory = MagicMock(return_value=mock_session)

    with (
        patch(
            "core.observer.db_queries.get_session_factory",
            return_value=mock_factory,
        ),
        patch(
            "core.observer.db_queries.get_observer_settings",
            new=AsyncMock(return_value=observer_settings),
        ),
    ):
        reason = await get_enable_queue_pause_reason()

    assert reason is None


# Проверяем что внешние изменения из БД перетирают устаревшее in-memory состояние
@pytest.mark.asyncio
async def test_refresh_runtime_ad_states_uses_db_as_source_of_truth():
    """Если Telegram перевёл объявление в CLAIMED, observer должен взять это из БД до нового скана."""
    from core.observer.db_queries import refresh_runtime_ad_states

    current_states = {"ad_001": (AlertState.WARNING_SENT, "old-token")}
    persisted_states = {"ad_001": (AlertState.CLAIMED, "old-token")}

    with patch(
        "core.observer.db_queries.load_ad_states_from_db",
        new=AsyncMock(return_value=persisted_states),
    ):
        refreshed = await refresh_runtime_ad_states(current_states)

    assert refreshed == persisted_states


# Проверяем что обычный ненулевой скан не инициализирует границу суток кабинета
@pytest.mark.asyncio
async def test_maybe_rollover_cabinet_day_waits_for_zero_scan():
    """До первого полного zero-scan cabinet_day_started_at не должен выставляться."""
    from core.observer.snapshot_writer import _maybe_rollover_cabinet_day

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
        "core.observer.snapshot_writer.get_or_create_observer_settings",
        new=AsyncMock(return_value=settings),
    ):
        await _maybe_rollover_cabinet_day(session, snapshot_data)

    assert settings.cabinet_day_started_at is None
    session.add.assert_not_called()


# --- Тесты reconnect (задача 2.4) ---


@pytest.mark.asyncio
async def test_reconnect_browser_manager_uses_db_vision_settings():
    """При reconnect должен обновить конфиг и переподключить browser-agent."""
    from apps.observer_worker.main import reconnect_browser_manager_with_vision_settings

    mock_grpc_client = AsyncMock()
    mock_grpc_client.config.vision_x_token = "old-token"
    mock_grpc_client.config.vision_api_url = "http://old:3030"
    mock_grpc_client.config.vision_profile_id = "old-profile"
    mock_grpc_client.reconnect_browser = AsyncMock()

    with (
        patch(
            "apps.observer_worker.main.load_vision_settings_for_runtime",
            new=AsyncMock(return_value=("db-token", "http://db:3030", "db-profile")),
        ),
    ):
        await reconnect_browser_manager_with_vision_settings(mock_grpc_client)

    assert mock_grpc_client.config.vision_x_token == "db-token"
    assert mock_grpc_client.config.vision_api_url == "http://db:3030"
    assert mock_grpc_client.config.vision_profile_id == "db-profile"
    mock_grpc_client.reconnect_browser.assert_awaited_once()


def test_is_browser_connection_error_filters_runtime_errors():
    """Reconnect-контур не должен маскировать произвольные RuntimeError."""
    from apps.observer_worker.main import _is_browser_connection_error

    assert _is_browser_connection_error(ConnectionError("Потеряна связь"))
    assert _is_browser_connection_error(
        RuntimeError("Target page, context or browser has been closed")
    )
    assert _is_browser_connection_error(OSError("Connection refused"))
    assert not _is_browser_connection_error(RuntimeError("Сбой Telegram"))


# Проверяем, что adaptive wait не теряет строки полного скана при частичном повторном чтении.
@pytest.mark.asyncio
async def test_wait_for_data_load_merges_partial_retry_rows():
    """Повторный poll с нижним фрагментом таблицы должен обновить данные, но сохранить исходные строки."""
    from apps.observer_worker.main import _wait_for_data_load
    from clients.python_grpc.client import ScanResult

    initial_rows = [
        SimpleNamespace(fb_ad_id="ad-1", spend=Decimal("0")),
        SimpleNamespace(fb_ad_id="ad-2", spend=Decimal("0")),
    ]
    retry_rows = [SimpleNamespace(fb_ad_id="ad-2", spend=Decimal("4.25"))]

    class Client:
        def __init__(self):
            self.scan_kwargs = None

        def run_scan_cycle(self, **kwargs):
            self.scan_kwargs = kwargs

            async def _events():
                yield ScanResult(rows=retry_rows, total_passes=1, duration_seconds=0.1)

            return _events()

    client = Client()

    rows = await _wait_for_data_load(
        client,
        prev_had_spend=True,
        initial_rows=initial_rows,
    )

    assert [row.fb_ad_id for row in rows] == ["ad-1", "ad-2"]
    assert rows[0].spend == Decimal("0")
    assert rows[1].spend == Decimal("4.25")
    assert client.scan_kwargs["do_refresh"] is False
    assert client.scan_kwargs["reset_scroll_first"] is True


def _patch_observer_loop_runtime(stack: ExitStack, *, scan_side_effect) -> AsyncMock:
    """Изолирует observer_loop от БД и внешних интеграций."""

    @asynccontextmanager
    async def _noop_browser_lock(**_kwargs):
        yield SimpleNamespace(waited_seconds=0.0)

    stack.enter_context(
        patch("apps.observer_worker.main.acquire_browser_lock", new=_noop_browser_lock)
    )
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
            "apps.observer_worker.main.reconcile_disable_tasks_in_db",
            new=AsyncMock(),
        )
    )
    stack.enter_context(
        patch(
            "apps.observer_worker.main.reconcile_enable_tasks_in_db",
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
            "apps.observer_worker.main.consume_scan_flags_combined",
            new=AsyncMock(return_value=(True, False, False)),
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
            "apps.observer_worker.main.get_enable_queue_pause_reason",
            new=AsyncMock(return_value=None),
        )
    )
    stack.enter_context(
        patch(
            "apps.observer_worker.main.load_fake_deposits",
            new=AsyncMock(return_value={}),
        )
    )
    stack.enter_context(patch("apps.observer_worker.main.batch_save_snapshots", new=AsyncMock()))
    stack.enter_context(
        patch("apps.observer_worker.main.auto_create_disable_tasks", new=AsyncMock())
    )
    stack.enter_context(
        patch(
            "apps.observer_worker.main.collect_reminder_alerts",
            new=AsyncMock(return_value=[]),
        )
    )
    stack.enter_context(
        patch("apps.observer_worker.main.broadcast_observer_runtime_message", new=AsyncMock())
    )
    stack.enter_context(
        patch("apps.observer_worker.main.update_observer_runtime_status", new=AsyncMock())
    )

    async def _noop_heartbeat(*_args, **_kwargs):
        # Замокать фоновый heartbeat-loop, чтобы он не крутился вечно
        # на mocked asyncio.sleep и не блокировал event loop в тестах.
        return None

    stack.enter_context(
        patch("apps.observer_worker.main._observer_heartbeat_loop", new=_noop_heartbeat)
    )
    stack.enter_context(
        patch(
            "apps.observer_worker.main.peek_scan_requested_flag",
            new=AsyncMock(return_value=False),
        )
    )

    stack.enter_context(patch("apps.observer_worker.main.random.uniform", return_value=0))
    stack.enter_context(patch("apps.observer_worker.main.compute_jitter", return_value=0))
    return stack.enter_context(
        patch("apps.observer_worker.main.asyncio.sleep", new_callable=AsyncMock)
    )


# Проверяем что observer вызывает gRPC run_scan_cycle
@pytest.mark.asyncio
async def test_observer_loop_delegates_scan_to_grpc():
    """Каждый цикл должен вызывать run_scan_cycle через gRPC client."""
    from apps.observer_worker.main import observer_loop

    shutdown_event = asyncio.Event()

    async def scan_cycle_generator():
        from clients.python_grpc.client import ScanResult

        shutdown_event.set()
        yield ScanResult(rows=[], total_passes=0, duration_seconds=0.0)

    mock_grpc_client = AsyncMock()
    mock_grpc_client.session_id = "test-session"
    mock_grpc_client.run_scan_cycle = scan_cycle_generator
    mock_grpc_client.validate_columns = AsyncMock(
        return_value={
            "valid": True,
            "missing_columns": [],
            "found_columns": [],
            "error_message": "",
        }
    )

    with ExitStack() as stack:
        _patch_observer_loop_runtime(stack, scan_side_effect=AsyncMock(return_value=[]))
        stack.enter_context(
            patch(
                "apps.observer_worker.main._wait_for_next_cycle",
                new=AsyncMock(return_value=False),
            )
        )
        await observer_loop(
            grpc_client=mock_grpc_client,
            offers={},
            telegram_bot_token="",
            telegram_chat_id="",
            shutdown_event=shutdown_event,
        )

    # gRPC client должен быть использован
    assert mock_grpc_client.session_id == "test-session"


# Проверяем, что observer создаёт задачу отключения по промежуточному событию и не ждёт конец полного сканирования.
@pytest.mark.asyncio
async def test_observer_loop_fast_stops_from_scan_progress():
    """STOP из промежуточного прохода должен досрочно завершить сканирование и создать задачу отключения."""
    from apps.observer_worker.main import observer_loop
    from clients.python_grpc.client import ScanProgress
    from core.observer.service import AlertCandidate

    shutdown_event = asyncio.Event()
    row = SimpleNamespace(fb_ad_id="ad-stop", spend=Decimal("12.00"))
    stop_alert = AlertCandidate(
        snapshot_id="incident-fast",
        offer_id=None,
        fb_ad_id="ad-stop",
        ad_name="Быстрый стоп",
        campaign_name="Кампания",
        adset_name="Группа",
        offer_code="DRC",
        offer_name=None,
        offer_cpa=None,
        stage=AlertStage.STOP,
        matched_rule_codes=["cpc_stop"],
        reason_title="CPC выше стопа",
        reason_text="CPC выше стопа",
        metrics_json={},
    )
    stop_snapshot = {
        "fb_ad_id": "ad-stop",
        "resolved_offer_code": "DRC",
        "delivery_status": "ACTIVE",
        "current_stage": AlertStage.STOP,
    }

    async def scan_cycle_generator(*_args, **_kwargs):
        yield ScanProgress(
            pass_number=1,
            rows_so_far=1,
            at_bottom=False,
            new_rows_count=1,
            new_rows=[row],
        )
        raise AssertionError("Observer должен остановить gRPC-поток после первого STOP")

    mock_grpc_client = AsyncMock()
    mock_grpc_client.session_id = "test-session"
    mock_grpc_client.run_scan_cycle = scan_cycle_generator
    mock_grpc_client.validate_columns = AsyncMock(
        return_value={
            "valid": True,
            "missing_columns": [],
            "found_columns": [],
            "error_message": "",
        }
    )
    run_scan_cycle_mock = AsyncMock(return_value=([stop_alert], [stop_alert], [stop_snapshot]))
    batch_save_mock = AsyncMock()
    lock_state = {"locked": False}

    async def auto_create_and_stop(_alerts):
        assert lock_state["locked"] is False
        shutdown_event.set()

    auto_create_mock = AsyncMock(side_effect=auto_create_and_stop)

    @asynccontextmanager
    async def fake_browser_lock(**_kwargs):
        lock_state["locked"] = True
        try:
            yield SimpleNamespace(waited_seconds=0.0)
        finally:
            lock_state["locked"] = False

    with ExitStack() as stack:
        _patch_observer_loop_runtime(stack, scan_side_effect=AsyncMock(return_value=[]))
        stack.enter_context(
            patch("apps.observer_worker.main.acquire_browser_lock", new=fake_browser_lock)
        )
        stack.enter_context(
            patch("apps.observer_worker.main._run_scan_cycle", new=run_scan_cycle_mock)
        )
        stack.enter_context(
            patch("apps.observer_worker.main.batch_save_snapshots", new=batch_save_mock)
        )
        stack.enter_context(
            patch("apps.observer_worker.main.auto_create_disable_tasks", new=auto_create_mock)
        )
        await observer_loop(
            grpc_client=mock_grpc_client,
            offers={},
            telegram_bot_token="",
            telegram_chat_id="",
            shutdown_event=shutdown_event,
        )

    run_scan_cycle_mock.assert_awaited_once()
    auto_create_mock.assert_awaited_once_with([stop_alert])
    assert any(
        call.kwargs.get("allow_cabinet_rollover") is False
        for call in batch_save_mock.await_args_list
    )
    assert any(
        call.kwargs.get("bypass_scan_guard") is True for call in batch_save_mock.await_args_list
    )


# Проверяем, что observer держит общую блокировку браузера во время scan.
@pytest.mark.asyncio
async def test_observer_loop_holds_browser_lock_during_scan():
    """Сканирование должно выполняться внутри общего lock, а не только после проверки очередей."""
    from apps.observer_worker.main import observer_loop
    from clients.python_grpc.client import ScanResult

    shutdown_event = asyncio.Event()
    lock_state = {"locked": False, "owners": []}

    @asynccontextmanager
    async def fake_browser_lock(**kwargs):
        lock_state["locked"] = True
        lock_state["owners"].append(kwargs.get("owner"))
        try:
            yield SimpleNamespace(waited_seconds=0.0)
        finally:
            lock_state["locked"] = False

    async def scan_cycle_generator(*_args, **_kwargs):
        assert lock_state["locked"] is True
        shutdown_event.set()
        yield ScanResult(
            rows=[SimpleNamespace(fb_ad_id="ad-001", spend=Decimal("1.00"))],
            total_passes=1,
            duration_seconds=0.0,
        )

    mock_grpc_client = AsyncMock()
    mock_grpc_client.session_id = "test-session"
    mock_grpc_client.run_scan_cycle = scan_cycle_generator
    mock_grpc_client.validate_columns = AsyncMock(
        return_value={
            "valid": True,
            "missing_columns": [],
            "found_columns": [],
            "error_message": "",
        }
    )

    with ExitStack() as stack:
        _patch_observer_loop_runtime(stack, scan_side_effect=AsyncMock(return_value=[]))
        stack.enter_context(
            patch("apps.observer_worker.main.acquire_browser_lock", new=fake_browser_lock)
        )
        stack.enter_context(
            patch(
                "apps.observer_worker.main._run_scan_cycle",
                new=AsyncMock(return_value=([], [], [])),
            )
        )
        stack.enter_context(
            patch(
                "apps.observer_worker.main._process_scan_results",
                new=AsyncMock(),
            )
        )
        stack.enter_context(
            patch(
                "apps.observer_worker.main._wait_for_next_cycle",
                new=AsyncMock(return_value=False),
            )
        )
        await observer_loop(
            grpc_client=mock_grpc_client,
            offers={},
            telegram_bot_token="",
            telegram_chat_id="",
            shutdown_event=shutdown_event,
        )

    assert lock_state["owners"] == ["observer-scan"]


# Проверяем что один пустой scan переводит observer в RECOVERING, но не выключает воркер
@pytest.mark.asyncio
async def test_observer_loop_recovers_after_single_empty_scan():
    """Один пустой цикл должен дать recovery-статус и повторить scan, а не выключать observer."""
    from apps.observer_worker.main import observer_loop
    from clients.python_grpc.client import ScanResult

    shutdown_event = asyncio.Event()
    scan_calls = 0

    async def scan_cycle_generator(*args, **kwargs):
        nonlocal scan_calls
        scan_calls += 1
        if scan_calls == 1:
            yield ScanResult(rows=[], total_passes=1, duration_seconds=0.0)
            return

        shutdown_event.set()
        yield ScanResult(
            rows=[SimpleNamespace(fb_ad_id="ad-001", spend=None)],
            total_passes=1,
            duration_seconds=0.0,
        )

    mock_grpc_client = AsyncMock()
    mock_grpc_client.session_id = "test-session"
    mock_grpc_client.run_scan_cycle = scan_cycle_generator
    mock_grpc_client.validate_columns = AsyncMock(
        return_value={
            "valid": True,
            "missing_columns": [],
            "found_columns": [],
            "error_message": "",
        }
    )

    update_runtime_status = AsyncMock()
    set_scanning_enabled = AsyncMock()

    with ExitStack() as stack:
        _patch_observer_loop_runtime(stack, scan_side_effect=AsyncMock(return_value=[]))
        stack.enter_context(
            patch(
                "apps.observer_worker.main._run_scan_cycle",
                new=AsyncMock(return_value=([], [], [])),
            )
        )
        stack.enter_context(
            patch(
                "apps.observer_worker.main._process_scan_results",
                new=AsyncMock(),
            )
        )
        stack.enter_context(
            patch(
                "apps.observer_worker.main._wait_for_next_cycle",
                new=AsyncMock(return_value=False),
            )
        )
        stack.enter_context(
            patch(
                "apps.observer_worker.main.update_observer_runtime_status",
                new=update_runtime_status,
            )
        )
        stack.enter_context(
            patch(
                "apps.observer_worker.main.set_observer_scanning_enabled",
                new=set_scanning_enabled,
            )
        )

        await observer_loop(
            grpc_client=mock_grpc_client,
            offers={},
            telegram_bot_token="",
            telegram_chat_id="",
            shutdown_event=shutdown_event,
        )

    assert scan_calls == 2
    set_scanning_enabled.assert_not_awaited()
    assert any(
        call.kwargs.get("status") == "RECOVERING" and "0 строк" in call.kwargs.get("message", "")
        for call in update_runtime_status.await_args_list
    )


# Проверяем, что пустая ошибка проверки колонок считается сбоем браузера, а не изменением layout.
@pytest.mark.asyncio
async def test_observer_loop_recovers_from_transient_column_validation_failure():
    """Если ValidateColumns не вернул детали из-за закрытой страницы, observer не должен отключать сканирование."""
    from apps.observer_worker.main import observer_loop

    shutdown_event = asyncio.Event()

    async def validate_columns():
        shutdown_event.set()
        return {
            "valid": False,
            "missing_columns": [],
            "found_columns": [],
            "error_message": "Ошибка валидации колонок: page.evaluate: Target page, context or browser has been closed",
        }

    mock_grpc_client = AsyncMock()
    mock_grpc_client.session_id = "test-session"
    mock_grpc_client.validate_columns = AsyncMock(side_effect=validate_columns)
    mock_grpc_client.reconnect_browser = AsyncMock()
    mock_grpc_client.run_scan_cycle = AsyncMock(
        side_effect=AssertionError("Скан не должен запускаться после сбоя ValidateColumns")
    )

    update_runtime_status = AsyncMock()
    set_scanning_enabled = AsyncMock()
    broadcast_runtime = AsyncMock()

    with ExitStack() as stack:
        _patch_observer_loop_runtime(stack, scan_side_effect=AsyncMock(return_value=[]))
        stack.enter_context(
            patch(
                "apps.observer_worker.main.update_observer_runtime_status",
                new=update_runtime_status,
            )
        )
        stack.enter_context(
            patch(
                "apps.observer_worker.main.set_observer_scanning_enabled",
                new=set_scanning_enabled,
            )
        )
        stack.enter_context(
            patch(
                "apps.observer_worker.main.broadcast_observer_runtime_message",
                new=broadcast_runtime,
            )
        )

        await observer_loop(
            grpc_client=mock_grpc_client,
            offers={},
            telegram_bot_token="",
            telegram_chat_id="",
            shutdown_event=shutdown_event,
        )

    mock_grpc_client.reconnect_browser.assert_awaited_once()
    set_scanning_enabled.assert_not_awaited()
    broadcast_runtime.assert_not_awaited()
    assert any(
        call.kwargs.get("status") == "RECOVERING"
        and "временной проблемой браузера/CDP" in call.kwargs.get("message", "")
        for call in update_runtime_status.await_args_list
    )


# Проверяем что несколько пустых scan подряд выключают observer с точной причиной
@pytest.mark.asyncio
async def test_observer_loop_pauses_after_consecutive_empty_scans():
    """После нескольких подряд пустых scan observer должен отключить сканирование и отправить честный алерт."""
    from apps.observer_worker.main import EMPTY_SCAN_FAILURE_LIMIT, observer_loop
    from clients.python_grpc.client import ScanResult

    shutdown_event = asyncio.Event()
    scan_calls = 0

    async def scan_cycle_generator(*args, **kwargs):
        nonlocal scan_calls
        scan_calls += 1
        yield ScanResult(rows=[], total_passes=1, duration_seconds=0.0)

    mock_grpc_client = AsyncMock()
    mock_grpc_client.session_id = "test-session"
    mock_grpc_client.run_scan_cycle = scan_cycle_generator
    mock_grpc_client.validate_columns = AsyncMock(
        return_value={
            "valid": True,
            "missing_columns": [],
            "found_columns": [],
            "error_message": "",
        }
    )

    update_runtime_status = AsyncMock()
    broadcast_runtime = AsyncMock()

    async def set_scanning_enabled(enabled: bool):
        if enabled is False:
            shutdown_event.set()

    set_scanning_enabled_mock = AsyncMock(side_effect=set_scanning_enabled)

    with ExitStack() as stack:
        _patch_observer_loop_runtime(stack, scan_side_effect=AsyncMock(return_value=[]))
        stack.enter_context(
            patch(
                "apps.observer_worker.main.update_observer_runtime_status",
                new=update_runtime_status,
            )
        )
        stack.enter_context(
            patch(
                "apps.observer_worker.main.broadcast_observer_runtime_message",
                new=broadcast_runtime,
            )
        )
        stack.enter_context(
            patch(
                "apps.observer_worker.main.set_observer_scanning_enabled",
                new=set_scanning_enabled_mock,
            )
        )

        await observer_loop(
            grpc_client=mock_grpc_client,
            offers={},
            telegram_bot_token="",
            telegram_chat_id="",
            shutdown_event=shutdown_event,
        )

    assert scan_calls == EMPTY_SCAN_FAILURE_LIMIT
    set_scanning_enabled_mock.assert_awaited_once_with(False)
    assert any(
        call.kwargs.get("status") == "PAUSED" and "0 строк" in call.kwargs.get("message", "")
        for call in update_runtime_status.await_args_list
    )
    broadcast_text = broadcast_runtime.await_args.kwargs["text"]
    assert "0 строк" in broadcast_text
    assert str(EMPTY_SCAN_FAILURE_LIMIT) in broadcast_text


# ScanDataUnavailableError корректно создаётся и содержит нужные поля
def test_scan_data_unavailable_error_has_correct_fields():
    """ScanDataUnavailableError должен содержать attempts и retry_interval_seconds."""
    from clients.python_grpc.client import ScanDataUnavailableError

    exc = ScanDataUnavailableError(
        attempts=3,
        retry_interval_seconds=10,
        reason="Ads Manager вернул 0 строк таблицы объявлений",
    )
    assert exc.attempts == 3
    assert exc.retry_interval_seconds == 10
    assert exc.reason == "Ads Manager вернул 0 строк таблицы объявлений"
    assert "0 строк" in str(exc)
    assert "3" in str(exc)


# --- Word-boundary offer matching ---


def test_resolve_offer_code_does_not_match_substring_inside_word():
    """Код оффера не должен совпадать как подстрока внутри буквенного слова."""
    from core.observer.service import resolve_offer_code

    offers = {"AB": object()}
    # "AB" является подстрокой "GRAB" — буква перед кодом → не должно матчиться
    assert resolve_offer_code("GRAB_test", "campaign", offers) is None


def test_resolve_offer_code_matches_at_word_start_with_underscore_separator():
    """Код должен матчиться когда отделён от остатка имени символом '_'."""
    from core.observer.service import resolve_offer_code

    offers = {"AB": object()}
    # "AB_creative_001": AB перед '_' — должно матчиться
    assert resolve_offer_code("AB_creative_001", "campaign", offers) == "AB"


def test_resolve_offer_code_matches_with_underscore_in_code():
    """Код вида DRC_CR2 должен матчиться как отдельный токен."""
    from core.observer.service import resolve_offer_code

    offers = {"DRC_CR2": object(), "DRC": object()}
    # Должен выбрать самый длинный совпадающий код
    assert resolve_offer_code("DRC_CR2_CR002", "CR2 | DRC | MV", offers) == "DRC_CR2"


def test_resolve_offer_code_case_insensitive():
    """Матчинг должен быть нечувствителен к регистру."""
    from core.observer.service import resolve_offer_code

    offers = {"DRC_CR2": object()}
    assert resolve_offer_code("drc_cr2_v3", "campaign", offers) == "DRC_CR2"


def test_resolve_offer_code_ad_name_priority_over_campaign():
    """Имя объявления приоритетнее имени кампании при конфликте кодов."""
    from core.observer.service import resolve_offer_code

    offers = {"KE_CR2": object(), "KEN_CR2": object()}
    # В объявлении KE_CR2, в кампании KEN_CR2 — должен победить код из объявления,
    # даже если код в кампании длиннее.
    assert resolve_offer_code("KE_CR2_CR001", "KEN_CR2 | main", offers) == "KE_CR2"


def test_resolve_offer_code_falls_back_to_campaign_when_ad_has_no_code():
    """Если в имени объявления кода нет — берём код из имени кампании."""
    from core.observer.service import resolve_offer_code

    offers = {"KEN_CR2": object()}
    assert resolve_offer_code("creative_001", "KEN_CR2 | mv", offers) == "KEN_CR2"


def test_resolve_offer_code_distinguishes_similar_prefixes():
    """Похожие префиксы (KE/KEN/CDR) различаются по word-boundary."""
    from core.observer.service import resolve_offer_code

    offers = {"CDR_CR2": object(), "KE_CR2": object(), "KEN_CR2": object()}
    assert resolve_offer_code("CDR_CR2_CR001", "campaign", offers) == "CDR_CR2"
    assert resolve_offer_code("KE_CR2_CR001", "campaign", offers) == "KE_CR2"
    assert resolve_offer_code("KEN_CR2_CR001", "campaign", offers) == "KEN_CR2"


# --- Per-offer пороги warning/stop ---


def test_build_rule_context_uses_per_offer_thresholds():
    """build_rule_context читает пороги напрямую из rule_config оффера."""
    from decimal import Decimal
    from types import SimpleNamespace

    from core.observer.service import build_rule_context

    rule_config = SimpleNamespace(
        cpc_percent_enabled=True,
        cpc_percent_stop=Decimal("2"),
        cpl_percent_enabled=True,
        cpl_percent_stop=Decimal("10"),
        cpr_percent_enabled=True,
        cpr_percent_stop=Decimal("20"),
        regs_no_dep_enabled=True,
        regs_no_dep_stop_count=5,
        spend_no_dep_enabled=True,
        spend_no_dep_from_percent=Decimal("50"),
        spend_no_dep_to_percent=Decimal("70"),
        spend_with_dep_enabled=True,
        spend_with_dep_from_percent=Decimal("70"),
        spend_with_dep_to_percent=Decimal("90"),
        warning_percent_of_stop=Decimal("80"),
        stop_percent_of_base=Decimal("100"),
        cpc_warning_percent_of_stop=Decimal("60"),
        cpc_stop_percent_of_base=Decimal("100"),
        cpl_warning_percent_of_stop=Decimal("80"),
        cpl_stop_percent_of_base=Decimal("50"),
        cpr_warning_percent_of_stop=Decimal("80"),
        cpr_stop_percent_of_base=Decimal("100"),
    )
    ctx = build_rule_context(
        cpa_amount=Decimal("5"),
        rule_config=rule_config,
    )
    assert ctx.cpc_warning_percent_of_stop == Decimal("60")
    assert ctx.cpl_stop_percent_of_base == Decimal("50")
    assert ctx.cpr_warning_percent_of_stop == Decimal("80")
    assert ctx.cpr_stop_percent_of_base == Decimal("100")
