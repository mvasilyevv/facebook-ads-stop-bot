# -*- coding: utf-8 -*-
"""Тесты каталога возможностей (Telegram /tools).

Главное — анти-расхождение: каталог должен покрывать ВСЕ tools реестра, иначе
при добавлении нового tool пользователь не увидит его в /tools.
"""

from __future__ import annotations

import core.ai_assistant.tools  # noqa: F401  заполняет GLOBAL_REGISTRY
from core.ai_assistant.catalog import (
    EXAMPLES,
    build_catalog_text,
    catalog_tool_names,
)
from core.ai_assistant.tools.registry import GLOBAL_REGISTRY


# Каталог покрывает ровно те же tools, что зарегистрированы — без лишних и без пропусков
def test_catalog_covers_all_registry_tools() -> None:
    registry_names = set(GLOBAL_REGISTRY.list_names())
    catalog_names = catalog_tool_names()
    missing = registry_names - catalog_names
    extra = catalog_names - registry_names
    assert not missing, f"В каталоге нет описаний для tools: {sorted(missing)}"
    assert not extra, f"В каталоге лишние tools (нет в реестре): {sorted(extra)}"


# create_campaign tool пока несовместим с реальным nested executor contract.
# Не рекламируем и не даём создать заведомо неисполнимый DRAFT до интеграции с wizard.
def test_unwired_create_campaign_tool_not_exposed() -> None:
    assert "request_create_campaign" not in set(GLOBAL_REGISTRY.list_names())


# Рендер не падает и содержит все категории + примеры
def test_build_catalog_text_has_sections() -> None:
    text = build_catalog_text()
    assert "Смотреть" in text  # READ_ONLY-категория
    assert "Действия" in text  # DRAFT_REQUIRED-категория
    assert "Креатив" in text  # CREATIVE-категория
    assert "/ask" in text
    # Каждый пример присутствует
    for ex in EXAMPLES:
        assert ex in text


# Все 15 tools реально попадают в текст (по описанию)
def test_catalog_text_mentions_every_tool_description() -> None:
    from core.ai_assistant.catalog import TOOL_DESCRIPTIONS

    text = build_catalog_text()
    for name in GLOBAL_REGISTRY.list_names():
        desc = TOOL_DESCRIPTIONS[name]
        assert desc in text, f"Описание tool '{name}' не попало в каталог"
