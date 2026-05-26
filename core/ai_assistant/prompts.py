# -*- coding: utf-8 -*-
"""Системные промпты для AI-помощника.

С wave 3 промпты вынесены в отдельную папку `core/ai_assistant/prompts/` —
один файл на роль. Доступ через `load_prompt(role)` с in-memory кешем.

Поддерживаемые роли:
- `operator` — главный промпт для /ask, AI-чата в UI и TMA (заменил INSTRUCTIONS.md).
- `analytics` — аналитический режим (глубокий разбор метрик через Marketing API READ).
- `creator_nl_parser` — NL-парсер для request_create_campaign (текст → spec_summary).
- `competitor_extraction` — анализ структуры креатива (analyze_creative).
- `ad_copy` — генерация вариантов текстов объявления (generate_ad_copy).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

# Папка с .md файлами промптов. Файл prompts.py лежит в core/ai_assistant/,
# а промпты — в core/ai_assistant/prompts/.
_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

# Legacy путь к INSTRUCTIONS.md — поддержка старого формата (для миграционного периода).
_INSTRUCTIONS_PATH_LEGACY = Path(__file__).resolve().parent / "INSTRUCTIONS.md"


class PromptNotFoundError(FileNotFoundError):
    """Промпт по указанной роли не найден."""


@lru_cache(maxsize=16)
def load_prompt(role: str) -> str:
    """Прочитать system prompt из `core/ai_assistant/prompts/{role}.md`.

    Кеш — на время жизни процесса. Чтобы перечитать после правки .md файла —
    вызвать `load_prompt.cache_clear()`.

    Args:
        role: имя файла без расширения (`operator`, `analytics`, `ad_copy`, ...).

    Returns:
        Содержимое файла с обрезанными пробелами по краям.

    Raises:
        PromptNotFoundError: если файла нет на диске.
    """
    if not role or "/" in role or "\\" in role or ".." in role:
        raise PromptNotFoundError(f"некорректное имя роли: {role!r}")

    path = _PROMPTS_DIR / f"{role}.md"
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise PromptNotFoundError(f"prompt {role!r} не найден ({path})") from exc


def load_instructions() -> str:
    """Legacy-функция. Возвращает текущий operator-промпт.

    Сохранена для обратной совместимости с тестами и старым кодом, который
    ожидал INSTRUCTIONS.md. Новый код должен использовать `load_prompt('operator')`.
    """
    try:
        return load_prompt("operator")
    except PromptNotFoundError:
        # Миграционный фолбэк на старый INSTRUCTIONS.md (на случай, если он ещё лежит).
        try:
            return _INSTRUCTIONS_PATH_LEGACY.read_text(encoding="utf-8").strip()
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
    """Системный промпт для интерактивного чата (operator-роль)."""
    try:
        return load_prompt("operator")
    except PromptNotFoundError:
        return (
            "Ты — AI-помощник FB Stop Bot. Помогаешь диагностировать и управлять системой "
            "через whitelisted tools. Отвечай коротко, по делу, на русском."
        )
