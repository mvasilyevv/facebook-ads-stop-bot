# -*- coding: utf-8 -*-
"""Тест AI-диагностики: cooldown и no-op при отсутствии ключей."""

from __future__ import annotations

import pytest

from core.ai_assistant import diagnostics
from core.ai_assistant.client import reset_ai_client_for_tests
from core.ai_assistant.diagnostics import diagnose_alert, reset_diagnose_cooldown_for_tests
from core.config import get_settings


# Сценарий: при отсутствии ANTHROPIC и OPENAI ключей diagnose_alert возвращает None.
@pytest.mark.asyncio
async def test_diagnose_no_op_without_keys(monkeypatch):
    reset_ai_client_for_tests()
    reset_diagnose_cooldown_for_tests()
    settings = get_settings()
    monkeypatch.setattr(settings, "anthropic_api_key", "", raising=False)
    monkeypatch.setattr(settings, "openai_api_key", "", raising=False)

    result = await diagnose_alert(alert_key="test:noop", context="x")
    assert result is None


# Сценарий: диагностика отключена флагом → None.
@pytest.mark.asyncio
async def test_diagnose_disabled_flag(monkeypatch):
    reset_ai_client_for_tests()
    reset_diagnose_cooldown_for_tests()
    settings = get_settings()
    monkeypatch.setattr(settings, "ai_diagnostics_enabled", False, raising=False)
    result = await diagnose_alert(alert_key="test:disabled", context="x")
    assert result is None


# Сценарий: повторный вызов внутри cooldown возвращает None.
@pytest.mark.asyncio
async def test_diagnose_cooldown(monkeypatch):
    reset_diagnose_cooldown_for_tests()
    monkeypatch.setattr(diagnostics, "_last_diagnose_at", {"k1": float(10**12)})
    settings = get_settings()
    monkeypatch.setattr(settings, "ai_diagnostics_enabled", True, raising=False)
    result = await diagnose_alert(alert_key="k1", context="x")
    assert result is None
