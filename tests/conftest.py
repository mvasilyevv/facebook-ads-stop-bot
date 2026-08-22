# -*- coding: utf-8 -*-
"""Конфигурация тестов."""

import os
import sys

os.environ.setdefault("ENCRYPTION_KEY", "UOGaDCkFFfSv7XMSdwQq_rqmossFFl8wSG7z69_5nO0=")
os.environ.setdefault(
    "ENCRYPTION_KEY_VERIFY",
    "gAAAAABqZwkRi9J37pVDxsdD0LHKWe_L6EkbhQVu1yKi_N43MdYL_I1IV_-5gsOOBXzCRMY9phj3dpLhDtQCsDcJPQKhEQjiRNeb6RuubyvM6vuxf6dgr30=",
)
os.environ.setdefault("TMA_SESSION_SECRET", "ci_tma_session_secret_0123456789abcdef")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "ci:test_token")
os.environ.setdefault("API_KEY", "ci_api_key")
os.environ.setdefault("ANTHROPIC_API_KEY", "ci_anthropic_key")
os.environ.setdefault("OPENAI_API_KEY", "ci_openai_key")

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
