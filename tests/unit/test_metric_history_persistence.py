# -*- coding: utf-8 -*-
"""Unit-тесты персистентности AdMetricHistory в observer-цикле."""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


def _scalars_result(rows):
    """Мок результата SQLAlchemy для scalars().all()."""
    result = MagicMock()
    result.scalars.return_value.all.return_value = rows
    return result


def _make_ad_item(
    fb_ad_id: str,
    *,
    spend: Decimal = Decimal("10.00"),
    clicks: int = 5,
    leads: int = 1,
    registrations: int = 0,
    deposits: int = 0,
    outbound_clicks: int = 4,
    landing_page_views: int = 3,
) -> dict:
    """Создаёт минимальный словарь данных снэпшота для тестов."""
    return {
        "fb_ad_id": fb_ad_id,
        "spend": spend,
        "clicks": clicks,
        "leads": leads,
        "registrations": registrations,
        "deposits": deposits,
        "outbound_clicks": outbound_clicks,
        "landing_page_views": landing_page_views,
        "cpc": None,
        "ctr": None,
        "cpm": None,
        "frequency": None,
        "cost_per_result": None,
        "cost_per_lead": None,
        "cost_per_registration": None,
        "outbound_ctr": None,
        "cost_per_landing_page_view": None,
        "reach": 0,
        "impressions": 0,
    }


@pytest.mark.asyncio
async def test_metric_history_writes_new_ad_without_existing_snapshot():
    """Новое объявление без снэпшота всегда записывается в историю."""
    from core.observer.snapshot_writer import _save_metric_deltas

    ad_id = uuid.uuid4()
    session = AsyncMock()
    # В БД нет текущих снэпшотов
    session.execute = AsyncMock(return_value=_scalars_result([]))

    item = _make_ad_item("ad-new")
    count = await _save_metric_deltas(session, [item], {"ad-new": ad_id})

    assert count == 1
    # Должен быть вызов INSERT (второй execute — сама вставка)
    assert session.execute.await_count == 2


@pytest.mark.asyncio
async def test_metric_history_skips_if_metrics_unchanged():
    """Если метрики не изменились относительно снэпшота — запись пропускается."""
    from core.observer.snapshot_writer import _save_metric_deltas

    ad_id = uuid.uuid4()
    old_snap = SimpleNamespace(
        fb_ad_id="ad-same",
        spend=Decimal("10.00"),
        clicks=5,
        leads=1,
        registrations=0,
        deposits=0,
        outbound_clicks=4,
        landing_page_views=3,
    )
    session = AsyncMock()
    session.execute = AsyncMock(return_value=_scalars_result([old_snap]))

    item = _make_ad_item("ad-same")
    count = await _save_metric_deltas(session, [item], {"ad-same": ad_id})

    # 0 записей — данные идентичны
    assert count == 0
    # Только SELECT (без INSERT)
    assert session.execute.await_count == 1


@pytest.mark.asyncio
async def test_metric_history_writes_when_spend_changed():
    """При изменении spend запись должна добавляться в историю."""
    from core.observer.snapshot_writer import _save_metric_deltas

    ad_id = uuid.uuid4()
    old_snap = SimpleNamespace(
        fb_ad_id="ad-grow",
        spend=Decimal("5.00"),
        clicks=3,
        leads=0,
        registrations=0,
        deposits=0,
        outbound_clicks=2,
        landing_page_views=1,
    )
    session = AsyncMock()
    session.execute = AsyncMock(return_value=_scalars_result([old_snap]))

    # spend вырос с 5 до 10
    item = _make_ad_item("ad-grow", spend=Decimal("10.00"), clicks=5)
    count = await _save_metric_deltas(session, [item], {"ad-grow": ad_id})

    assert count == 1
    assert session.execute.await_count == 2


@pytest.mark.asyncio
async def test_metric_history_skips_cumulative_regression():
    """Откат spend/clicks назад блокируется как подозрительная регрессия."""
    from core.observer.snapshot_writer import _save_metric_deltas

    ad_id = uuid.uuid4()
    old_snap = SimpleNamespace(
        fb_ad_id="ad-regress",
        spend=Decimal("20.00"),
        clicks=100,
        leads=5,
        registrations=2,
        deposits=1,
        outbound_clicks=80,
        landing_page_views=50,
    )
    session = AsyncMock()
    session.execute = AsyncMock(return_value=_scalars_result([old_snap]))

    # spend упал — это регрессия (например сброс кабинета ещё не подтверждён)
    item = _make_ad_item(
        "ad-regress",
        spend=Decimal("0.00"),
        clicks=0,
        leads=0,
        registrations=0,
        deposits=0,
        outbound_clicks=0,
        landing_page_views=0,
    )
    count = await _save_metric_deltas(session, [item], {"ad-regress": ad_id})

    # Регрессия заблокирована — ни одной строки
    assert count == 0
    # Только SELECT без INSERT
    assert session.execute.await_count == 1


@pytest.mark.asyncio
async def test_metric_history_spend_zero_reset_accepted_after_three_cycles():
    """После 3 последовательных циклов с регрессией RegressionGuard принимает новый базовый снимок."""
    from core.observer.regression_guard import RegressionGuard
    from core.observer.snapshot_writer import _save_metric_deltas

    ad_id = uuid.uuid4()
    old_snap = SimpleNamespace(
        fb_ad_id="ad-zero",
        spend=Decimal("50.00"),
        clicks=200,
        leads=10,
        registrations=5,
        deposits=2,
        outbound_clicks=150,
        landing_page_views=100,
    )

    # spend упал — эмулируем 3 цикла подряд
    item = _make_ad_item(
        "ad-zero",
        spend=Decimal("0.02"),
        clicks=0,
        leads=0,
        registrations=0,
        deposits=0,
        outbound_clicks=0,
        landing_page_views=0,
    )

    guard = RegressionGuard()

    for cycle in range(1, 4):
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_scalars_result([old_snap]))
        count = await _save_metric_deltas(
            session, [item], {"ad-zero": ad_id}, regression_guard=guard
        )
        if cycle < 3:
            # Первые два цикла блокируются
            assert count == 0, f"Цикл {cycle}: ожидался skip, но записано {count}"
        else:
            # На третий цикл guard форсирует принятие
            assert count == 1, f"Цикл 3: ожидалась запись, но count={count}"


@pytest.mark.asyncio
async def test_metric_history_empty_ad_id_map_returns_zero():
    """Пустой ad_id_map означает отсутствие объявлений в реестре — история не пишется."""
    from core.observer.snapshot_writer import _save_metric_deltas

    session = AsyncMock()
    item = _make_ad_item("ad-x")
    count = await _save_metric_deltas(session, [item], ad_id_map={})

    # Нет маппинга → нет записей и нет запросов к БД
    assert count == 0
    session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_metric_history_multiple_ads_partial_change():
    """Из нескольких объявлений в историю попадают только те, у которых изменились метрики."""
    from core.observer.snapshot_writer import _save_metric_deltas

    ad_id_a = uuid.uuid4()
    ad_id_b = uuid.uuid4()

    # ad-a: spend вырос, должен записаться
    snap_a = SimpleNamespace(
        fb_ad_id="ad-a",
        spend=Decimal("5.00"),
        clicks=3,
        leads=0,
        registrations=0,
        deposits=0,
        outbound_clicks=2,
        landing_page_views=1,
    )
    # ad-b: без изменений, должен быть пропущен
    snap_b = SimpleNamespace(
        fb_ad_id="ad-b",
        spend=Decimal("10.00"),
        clicks=5,
        leads=1,
        registrations=0,
        deposits=0,
        outbound_clicks=4,
        landing_page_views=3,
    )

    session = AsyncMock()
    session.execute = AsyncMock(return_value=_scalars_result([snap_a, snap_b]))

    items = [
        _make_ad_item("ad-a", spend=Decimal("12.00"), clicks=8),  # изменился
        _make_ad_item("ad-b"),  # идентичен snap_b
    ]
    ad_id_map = {"ad-a": ad_id_a, "ad-b": ad_id_b}
    count = await _save_metric_deltas(session, items, ad_id_map)

    # Только одно объявление с изменениями
    assert count == 1
    assert session.execute.await_count == 2
