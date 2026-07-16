# -*- coding: utf-8 -*-
"""Тесты слоя скилов: load_skill + build_chat_system_prompt(skills=...)."""

from __future__ import annotations

import pytest

from core.ai_assistant.prompts import (
    PromptNotFoundError,
    build_chat_system_prompt,
    load_skill,
)


# Существующий v1-скил читается с диска и не пустой
def test_load_skill_reads_existing() -> None:
    text = load_skill("chat_operator")
    assert "Telegram" in text
    assert len(text) > 100


# Несуществующий скил — явная PromptNotFoundError, а не пустая строка
def test_load_skill_missing_raises() -> None:
    with pytest.raises(PromptNotFoundError):
        load_skill("no_such_skill_xyz")


# Path-guard: попытка выйти из каталога скилов отклоняется
@pytest.mark.parametrize("bad", ["../operator", "a/b", "a\\b", "", ".."])
def test_load_skill_path_guard(bad: str) -> None:
    with pytest.raises(PromptNotFoundError):
        load_skill(bad)


# Без skills промпт равен базовому operator-промпту (обратная совместимость)
def test_build_prompt_without_skills_is_base() -> None:
    base = build_chat_system_prompt()
    assert build_chat_system_prompt(skills=None) == base
    assert build_chat_system_prompt(skills=[]) == base


# Со скилом текст скила подмешан после базы через разделитель
def test_build_prompt_appends_skill() -> None:
    combined = build_chat_system_prompt(skills=["curator_case"])
    assert combined.startswith(build_chat_system_prompt())
    assert "кейс куратора" in combined
    assert "\n\n---\n\n" in combined


# Отсутствующий скил не роняет сборку промпта — просто пропускается
def test_build_prompt_skips_missing_skill() -> None:
    combined = build_chat_system_prompt(skills=["no_such_skill_xyz", "pulse_report"])
    assert "пульс" in combined.lower() or "отчёт" in combined.lower()
    assert combined.startswith(build_chat_system_prompt())


def test_web_chat_skill_requires_spend_and_status_tools() -> None:
    combined = build_chat_system_prompt(skills=["web_chat"])

    assert "get_insights" in combined
    assert "find_ads" in combined
    assert "Не заканчивай ответ после одного `get_account_health`" in combined
