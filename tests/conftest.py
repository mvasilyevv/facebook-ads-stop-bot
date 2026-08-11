# -*- coding: utf-8 -*-
"""Конфигурация тестов."""

import sys

import pytest

from core.config import get_settings

sys.dont_write_bytecode = True


@pytest.fixture(autouse=True)
def _disable_api_key_auth(monkeypatch):
    """H-3: тесты не шлют X-API-Key — отключаем enforcement глобально.

    Прод secure-by-default (require_api_key=True). Enforcement как таковой
    проверяется отдельным unit-тестом test_api_key_auth.py со своим settings.
    """
    monkeypatch.setattr(get_settings(), "require_api_key", False, raising=False)
