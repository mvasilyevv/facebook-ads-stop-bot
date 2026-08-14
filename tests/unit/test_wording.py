# -*- coding: utf-8 -*-
"""Склонение числительных и русские названия операций для карточек."""

from __future__ import annotations

import pytest

from core.wording import (
    action_label_ru,
    ads_ru,
    clicks_ru,
    counted_ru,
    delivery_status_ru,
    deposits_ru,
    minutes_ru,
    plural_ru,
    registrations_ru,
    times_ru,
)


# 1 / 2-4 / 5+ — три формы, иначе оператор читает «43 кликов» и «1 объявлений».
@pytest.mark.parametrize(
    ("count", "expected"),
    [
        (1, "клик"),
        (2, "клика"),
        (3, "клика"),
        (4, "клика"),
        (5, "кликов"),
        (0, "кликов"),
    ],
)
def test_plural_ru_covers_one_few_many(count: int, expected: str) -> None:
    assert plural_ru(count, "клик", "клика", "кликов") == expected


# 11-14 — ловушка: заканчиваются на 1-4, но требуют формы «много».
@pytest.mark.parametrize("count", [11, 12, 13, 14, 111, 112, 114])
def test_plural_ru_teens_use_many_form(count: int) -> None:
    assert plural_ru(count, "клик", "клика", "кликов") == "кликов"


@pytest.mark.parametrize(
    ("count", "expected"),
    [(21, "клик"), (22, "клика"), (25, "кликов"), (101, "клик"), (104, "клика")],
)
def test_plural_ru_follows_last_digit_after_twenty(count: int, expected: str) -> None:
    assert plural_ru(count, "клик", "клика", "кликов") == expected


def test_counted_ru_keeps_number_next_to_word() -> None:
    assert counted_ru(1, "клик", "клика", "кликов") == "1 клик"
    assert counted_ru(2, "клик", "клика", "кликов") == "2 клика"
    assert counted_ru(5, "клик", "клика", "кликов") == "5 кликов"


# Подтверждённый ноль пишется словами: «депозитов нет» вместо «0 депозитов».
def test_zero_metrics_read_as_words() -> None:
    assert clicks_ru(0) == "кликов нет"
    assert registrations_ru(0) == "регистраций нет"
    assert deposits_ru(0) == "депозитов нет"


def test_metric_counters_agree_with_number() -> None:
    assert clicks_ru(1) == "1 клик"
    assert clicks_ru(43) == "43 клика"
    assert registrations_ru(2) == "2 регистрации"
    assert registrations_ru(5) == "5 регистраций"
    assert deposits_ru(1) == "1 депозит"
    assert ads_ru(1) == "1 объявление"
    assert ads_ru(11) == "11 объявлений"
    assert minutes_ru(21) == "21 минута"
    assert times_ru(44) == "44 раза"


# Оператор видит операцию по-русски, а не внутренний mutation_kind.
def test_action_labels_are_human() -> None:
    assert action_label_ru("pause_ad") == "отключение объявления"
    assert action_label_ru("activate_ad") == "включение объявления"
    assert action_label_ru("duplicate_adset_structure") == "дублирование адсетов"
    assert action_label_ru("") == "действие"
    # Неизвестный kind не подменяется выдуманным названием.
    assert action_label_ru("future_kind") == "future_kind"


def test_delivery_status_is_human() -> None:
    assert delivery_status_ru("ACTIVE") == "включённое"
    assert delivery_status_ru("paused") == "выключенное"
    assert delivery_status_ru("SOMETHING_NEW") == "SOMETHING_NEW"
