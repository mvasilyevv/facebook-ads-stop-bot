# -*- coding: utf-8 -*-
"""Свежесть списка кампаний allowlist-пикера (решение владельца 03.07).

Кампании с датой запуска в имени старше CAMPAIGN_LIST_HORIZON_DAYS (14) прячутся
из списка, если не выбраны в allowlist. Дата парсится из названия (нейминг-SOP:
DD.MM[.YY] в конце), год обычно опущен — выводится с учётом границы года.
"""

from __future__ import annotations

from datetime import date

from apps.api.routers.v1.schemas.settings_observer import CampaignOption
from apps.api.routers.v1.settings_observer import (
    CAMPAIGN_LIST_HORIZON_DAYS,
    _campaign_name_date,
    _filter_stale_options,
    _is_stale_campaign,
)

_TODAY = date(2026, 7, 3)


# Дата без года «28.06» летом → текущий год
def test_name_date_without_year_current():
    assert _campaign_name_date("MV | GH_CR | adset.pro | 28.06 | 2", today=_TODAY) == date(
        2026, 6, 28
    )


# «Завтрашняя» дата допустима (нейминг today+1) — год не откатывается
def test_name_date_tomorrow_stays_current_year():
    assert _campaign_name_date("MV | X | 04.07", today=_TODAY) == date(2026, 7, 4)


# Граница года: «28.12» в январе → прошлый год, а не будущий декабрь
def test_name_date_cross_year_december_in_january():
    assert _campaign_name_date("MV | X | 28.12", today=date(2027, 1, 5)) == date(2026, 12, 28)


# Явный год «22.05.25» парсится как 2025
def test_name_date_explicit_two_digit_year():
    assert _campaign_name_date("MV | X | 22.05.25", today=_TODAY) == date(2025, 5, 22)


# Невалидная дата (32.13) и имя без даты → None
def test_name_date_invalid_or_missing():
    assert _campaign_name_date("MV | X | 32.13", today=_TODAY) is None
    assert _campaign_name_date("MV | X | без даты", today=_TODAY) is None


# Свежая (в пределах горизонта) — не stale; старше горизонта — stale; без даты — не stale
def test_is_stale_boundaries():
    fresh = _TODAY.replace(day=1)  # 2 дня назад от 03.07 → внутри 14 дней
    assert _is_stale_campaign(f"MV | X | {fresh.day:02d}.{fresh.month:02d}", today=_TODAY) is False
    assert _is_stale_campaign("MV | X | 22.05", today=_TODAY) is True  # 42 дня
    assert _is_stale_campaign("MV | X | имя без даты", today=_TODAY) is False


# Фильтр: старые невыбранные прячутся, старые ВЫБРАННЫЕ остаются (их снимают тут же),
# свежие и бездатные остаются
def test_filter_keeps_selected_and_fresh(monkeypatch):
    options = [
        CampaignOption(id="1", name="MV | GH_CR | 28.06 | 2", selected=False),  # свежая
        CampaignOption(id="2", name="MV | KE | CR2 | 22.05 | 1", selected=False),  # старая
        CampaignOption(id="3", name="MV | KE | CR2 | 22.05 | 2", selected=True),  # старая выбранная
        CampaignOption(id="4", name="MV | без даты", selected=False),
    ]
    import apps.api.routers.v1.settings_observer as mod

    class _FakeDateTime:
        @staticmethod
        def now(tz=None):
            import datetime as _dt

            return _dt.datetime(2026, 7, 3, 12, 0, tzinfo=tz)

    monkeypatch.setattr(mod, "datetime", _FakeDateTime)
    kept = {o.id for o in _filter_stale_options(options)}
    assert kept == {"1", "3", "4"}
    assert CAMPAIGN_LIST_HORIZON_DAYS == 14
