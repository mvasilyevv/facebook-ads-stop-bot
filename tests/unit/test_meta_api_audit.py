# -*- coding: utf-8 -*-
"""Unit-тесты core.meta_api.audit — pure-функция extract_ad_account_id_from_endpoint."""

from __future__ import annotations

from core.meta_api.audit import extract_ad_account_id_from_endpoint


# Стандартный insights endpoint содержит act_XXX в начале пути.
def test_extract_from_insights_endpoint() -> None:
    assert extract_ad_account_id_from_endpoint("/act_123456/insights") == "act_123456"


# Endpoint без act_ — None.
def test_extract_from_me_endpoint() -> None:
    assert extract_ad_account_id_from_endpoint("/me") is None
    assert extract_ad_account_id_from_endpoint("/me/adaccounts") is None


# Глубокий endpoint с act_ в середине пути.
def test_extract_nested_endpoint() -> None:
    assert extract_ad_account_id_from_endpoint("/act_999/ads/12345/insights") == "act_999"


# Пустая строка — None.
def test_extract_empty() -> None:
    assert extract_ad_account_id_from_endpoint("") is None


# act_ должен быть слитно с цифрами; "actually" не считается.
def test_extract_no_false_positive() -> None:
    assert extract_ad_account_id_from_endpoint("/actually_not_an_account/") is None
