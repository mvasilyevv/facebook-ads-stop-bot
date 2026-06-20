# -*- coding: utf-8 -*-
"""Юнит-тесты normalize_web_app_base — нормализация base для deep-link кнопок."""

from __future__ import annotations

import pytest

from core.telegram.web_app_url import normalize_web_app_base


# https-base: обрезаются пробелы и хвостовой слэш
def test_https_strips_whitespace_and_trailing_slash():
    assert normalize_web_app_base("  https://h.ts.net/tma/  ") == "https://h.ts.net/tma"


# https без хвостового слэша возвращается как есть
def test_https_passthrough():
    assert normalize_web_app_base("https://h.ts.net/tma") == "https://h.ts.net/tma"


# http (не https) отвергается → None (Telegram требует https)
def test_http_rejected():
    assert normalize_web_app_base("http://h.ts.net/tma") is None


# пусто / None / только пробелы → None
@pytest.mark.parametrize("raw", [None, "", "   "])
def test_empty_returns_none(raw):
    assert normalize_web_app_base(raw) is None
