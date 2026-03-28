# -*- coding: utf-8 -*-
"""Тесты диагностики CPM и частоты."""

from __future__ import annotations

from decimal import Decimal

from core.diagnostics import build_ad_quality_diagnostics, compute_cpm_baselines_by_offer


# Проверяем что медиана CPM считается по активным объявлениям одного оффера.
def test_compute_cpm_baselines_by_offer_uses_median():
    items = [
        {"offer": "offer_a", "cpm": Decimal("10.00")},
        {"offer": "offer_a", "cpm": Decimal("12.00")},
        {"offer": "offer_a", "cpm": Decimal("14.00")},
        {"offer": "offer_b", "cpm": Decimal("20.00")},
        {"offer": "offer_b", "cpm": Decimal("30.00")},
    ]

    baselines = compute_cpm_baselines_by_offer(
        items,
        offer_code_getter=lambda item: item["offer"],
        cpm_getter=lambda item: item["cpm"],
    )

    assert baselines["offer_a"] == Decimal("12.0000")
    assert "offer_b" not in baselines


# Проверяем что для CPM без базы из трёх объявлений возвращается статус недостатка данных.
def test_build_ad_quality_diagnostics_handles_missing_cpm_baseline():
    diagnostics = build_ad_quality_diagnostics(
        cpm_value=Decimal("11.50"),
        cpm_baseline=None,
        frequency_value=Decimal("1.50"),
        frequency_elevated_threshold=Decimal("2.00"),
        frequency_critical_threshold=Decimal("3.00"),
    )

    assert diagnostics.cpm.status == "insufficient_data"
    assert "не хватает минимум трёх активных объявлений" in diagnostics.cpm.text


# Проверяем что CPM выше 140% медианы помечается как критичный.
def test_build_ad_quality_diagnostics_marks_cpm_as_critical():
    diagnostics = build_ad_quality_diagnostics(
        cpm_value=Decimal("15.00"),
        cpm_baseline=Decimal("10.00"),
        frequency_value=Decimal("1.00"),
        frequency_elevated_threshold=Decimal("2.00"),
        frequency_critical_threshold=Decimal("3.00"),
    )

    assert diagnostics.cpm.status == "critical"
    assert diagnostics.cpm.bar_percent == 100
    assert diagnostics.summary_text == "Главный риск сейчас в дорогом аукционе по CPM."


# Проверяем что частота использует две офферные границы и даёт человекочитаемый вывод.
def test_build_ad_quality_diagnostics_marks_frequency_as_elevated():
    diagnostics = build_ad_quality_diagnostics(
        cpm_value=Decimal("10.00"),
        cpm_baseline=Decimal("10.00"),
        frequency_value=Decimal("2.40"),
        frequency_elevated_threshold=Decimal("2.00"),
        frequency_critical_threshold=Decimal("3.00"),
    )

    assert diagnostics.frequency.status == "elevated"
    assert "выше рабочей нормы 2.00" in diagnostics.frequency.text
    assert diagnostics.summary_text == "Главный риск сейчас в выгорании аудитории по частоте."
