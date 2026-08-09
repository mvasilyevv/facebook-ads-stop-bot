# -*- coding: utf-8 -*-
"""Системные промпты для AI-помощника.

С wave 3 промпты вынесены в отдельную папку `core/ai_assistant/prompts/` —
один файл на роль. Доступ через `load_prompt(role)` с in-memory кешем.

Поддерживаемые роли:
- `operator` — главный промпт для /ask, AI-чата в UI и TMA (заменил INSTRUCTIONS.md).
- `analytics` — аналитический режим (глубокий разбор метрик через Marketing API READ).
- `competitor_extraction` — анализ структуры креатива (analyze_creative).
- `ad_copy` — генерация вариантов текстов объявления (generate_ad_copy).
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

# Папка с .md файлами промптов. Файл prompts.py лежит в core/ai_assistant/,
# а промпты — в core/ai_assistant/prompts/.
_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

# Скилы — узкие доменные добавки к системному промпту (по одной на сценарий).
_SKILLS_DIR = _PROMPTS_DIR / "skills"


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


@lru_cache(maxsize=32)
def load_skill(name: str) -> str:
    """Прочитать скил из `core/ai_assistant/prompts/skills/{name}.md`.

    Скил — доменная добавка к системному промпту (кейс куратора, пульс-отчёт,
    правила TG-чата). Тот же контракт, что у `load_prompt`: кеш на процесс,
    path-guard против выхода из каталога.
    """
    if not name or "/" in name or "\\" in name or ".." in name:
        raise PromptNotFoundError(f"некорректное имя скила: {name!r}")

    path = _SKILLS_DIR / f"{name}.md"
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise PromptNotFoundError(f"skill {name!r} не найден ({path})") from exc


SYSTEM_PROMPT_DIAGNOSTICS = """Ты — диагностический ассистент FB Agent.

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


def build_chat_system_prompt(skills: tuple[str, ...] | list[str] | None = None) -> str:
    """Системный промпт для интерактивного чата (operator-роль + опциональные скилы).

    skills — имена файлов из `prompts/skills/` (без .md); отсутствующий скил
    не роняет чат — warning в лог и пропуск.
    """
    parts = [load_prompt("operator")]
    for name in skills or ():
        try:
            parts.append(load_skill(name))
        except PromptNotFoundError:
            logger.warning("Скил %r не найден — пропускаю (промпт без него)", name)
    return "\n\n---\n\n".join(parts)
