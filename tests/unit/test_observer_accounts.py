# -*- coding: utf-8 -*-
"""Unit-тесты резолва scan set'а кабинетов (core/observer/accounts.py, мульти-кабинет M1)."""

from __future__ import annotations

import pytest

from core.observer.accounts import normalize_account_id


# Числовой ID проходит без изменений.
def test_normalize_plain_numeric() -> None:
    assert normalize_account_id("1234567890") == "1234567890"


# Префикс act_ срезается (любой регистр) — в БД храним только цифры.
@pytest.mark.parametrize("raw", ["act_555", "ACT_555", "Act_555"])
def test_normalize_strips_act_prefix(raw: str) -> None:
    assert normalize_account_id(raw) == "555"


# Пробелы по краям не мешают нормализации.
def test_normalize_trims_whitespace() -> None:
    assert normalize_account_id("  act_42  ") == "42"


# Мусор (буквы, пусто, None, отрицательные, дробные) отбрасывается в None.
@pytest.mark.parametrize("raw", [None, "", "  ", "abc", "act_", "12a3", "-5", "1.5"])
def test_normalize_rejects_garbage(raw: str | None) -> None:
    assert normalize_account_id(raw) is None
