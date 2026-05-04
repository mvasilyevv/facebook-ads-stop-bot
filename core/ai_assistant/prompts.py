# -*- coding: utf-8 -*-
"""Системные промпты для AI-помощника."""

from __future__ import annotations

from pathlib import Path

_INSTRUCTIONS_PATH = Path(__file__).resolve().parent / "INSTRUCTIONS.md"


def load_instructions() -> str:
    """Читает INSTRUCTIONS.md (или возвращает пустую строку, если файла нет)."""
    try:
        return _INSTRUCTIONS_PATH.read_text(encoding="utf-8")
    except OSError:
        return ""


SYSTEM_PROMPT_DIAGNOSTICS = """Ты — диагностический ассистент FB Stop Bot.

Тебе пришёл критический алерт о том, что авто-восстановление не помогло.
На вход получишь:
- alert_key (тип проблемы);
- log_excerpt (последние строки лога, если доступны);
- context (краткий human-readable контекст).

Твоя задача:
1. Назвать вероятную причину одной короткой фразой.
2. Сказать, что нужно сделать пользователю (если нужно).
3. Если ничего внешнего не нужно — так и сказать.

Формат: 2-4 строки HTML (теги <b>, <i>, <code>). Без воды, без преамбул.
Не повторяй текст исходного алерта."""


def build_chat_system_prompt() -> str:
    """Системный промпт для интерактивного чата (включает INSTRUCTIONS.md)."""
    instructions = load_instructions()
    if instructions:
        return instructions
    return (
        "Ты — AI-помощник FB Stop Bot. Помогаешь диагностировать и управлять системой "
        "через whitelisted tools. Отвечай коротко, по делу, на русском."
    )
