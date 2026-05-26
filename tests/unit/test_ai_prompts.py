# -*- coding: utf-8 -*-
"""Unit-тесты для core/ai_assistant/prompts.py — загрузка system-промптов из файлов."""

from __future__ import annotations

import pytest

from core.ai_assistant.prompts import (
    SYSTEM_PROMPT_DIAGNOSTICS,
    PromptNotFoundError,
    build_chat_system_prompt,
    load_instructions,
    load_prompt,
)

# ─── load_prompt ───────────────────────────────────────────────────────────


# Сценарий: загрузка существующего промпта возвращает непустой текст.
def test_load_prompt_operator_returns_text():
    text = load_prompt("operator")
    assert text
    assert len(text) > 200
    # operator.md должен содержать упоминание ключевых tools
    assert "tail_log" in text
    assert "get_insights" in text


# Сценарий: все 5 ролей доступны (operator, analytics, creator_nl_parser, competitor_extraction, ad_copy).
@pytest.mark.parametrize(
    "role",
    [
        "operator",
        "analytics",
        "creator_nl_parser",
        "competitor_extraction",
        "ad_copy",
    ],
)
def test_load_prompt_all_known_roles_available(role: str):
    text = load_prompt(role)
    assert text
    assert len(text) > 100, f"prompt {role!r} подозрительно короткий: {len(text)} символов"


# Сценарий: повторный вызов load_prompt возвращает тот же объект (кеш через lru_cache).
def test_load_prompt_uses_cache():
    text1 = load_prompt("operator")
    text2 = load_prompt("operator")
    assert text1 is text2  # один и тот же объект из кеша


# Сценарий: несуществующая роль → PromptNotFoundError.
def test_load_prompt_unknown_role_raises():
    with pytest.raises(PromptNotFoundError, match="prompt"):
        load_prompt("definitely_does_not_exist_xyz")


# Сценарий: path-traversal в имени роли блокируется.
@pytest.mark.parametrize(
    "role",
    [
        "../etc/passwd",
        "..\\windows",
        "subdir/file",
        "",
    ],
)
def test_load_prompt_rejects_path_traversal(role: str):
    with pytest.raises(PromptNotFoundError):
        load_prompt(role)


# ─── build_chat_system_prompt ──────────────────────────────────────────────


# Сценарий: build_chat_system_prompt возвращает operator-промпт.
def test_build_chat_system_prompt_returns_operator():
    prompt = build_chat_system_prompt()
    assert prompt
    # должен содержать сигнатуры operator-промпта
    assert "FB Stop Bot" in prompt
    assert "tail_log" in prompt


# ─── load_instructions (legacy) ────────────────────────────────────────────


# Сценарий: legacy load_instructions() возвращает тот же текст, что load_prompt("operator").
def test_load_instructions_legacy_returns_operator_prompt():
    legacy = load_instructions()
    new = load_prompt("operator")
    assert legacy == new


# ─── SYSTEM_PROMPT_DIAGNOSTICS ─────────────────────────────────────────────


# Сценарий: диагностический промпт по-прежнему доступен и содержит формат.
def test_diagnostics_prompt_contains_format_hint():
    assert SYSTEM_PROMPT_DIAGNOSTICS
    assert "HTML" in SYSTEM_PROMPT_DIAGNOSTICS
    assert "FB Stop Bot" in SYSTEM_PROMPT_DIAGNOSTICS


# ─── Контент промптов: operator должен описывать все 15 tools ──────────────


# Сценарий: operator.md упоминает все имена tools (snake_case) — гарантия, что
# при добавлении нового tool промпт не отстаёт от реестра.
def test_operator_prompt_mentions_all_tool_names():
    text = load_prompt("operator")
    expected_tools = [
        "tail_log",
        "api_get",
        "supervisor_restart",
        "set_scanning",
        "get_insights",
        "find_ads",
        "get_offer_performance",
        "get_account_health",
        "get_competitor_patterns",
        "request_budget_change",
        "request_clone_campaign",
        "request_bulk_pause",
        "request_create_campaign",
        "generate_ad_copy",
        "analyze_creative",
    ]
    missing = [t for t in expected_tools if t not in text]
    assert not missing, f"operator.md не упоминает tools: {missing}"


# Сценарий: ad_copy.md описывает формат ответа (JSON-массив с полями).
def test_ad_copy_prompt_describes_output_format():
    text = load_prompt("ad_copy")
    assert "primary_text" in text
    assert "headline" in text
    assert "predicted_hook_strength" in text
    assert "JSON" in text


# Сценарий: competitor_extraction.md описывает все поля анализа.
def test_competitor_extraction_prompt_describes_fields():
    text = load_prompt("competitor_extraction")
    for field in (
        "hook",
        "hook_type",
        "pain_point",
        "value_prop",
        "proof_elements",
        "policy_risk",
    ):
        assert field in text, f"competitor_extraction.md не упоминает поле {field}"


# Сценарий: creator_nl_parser.md описывает целевые поля spec_summary.
def test_creator_nl_parser_describes_target_schema():
    text = load_prompt("creator_nl_parser")
    for field in (
        "offer_code",
        "countries",
        "daily_budget_usd",
        "objective",
        "attribution_days",
    ):
        assert field in text, f"creator_nl_parser.md не упоминает поле {field}"
    # Должен указать формат ошибок
    assert "_errors" in text
