# -*- coding: utf-8 -*-
"""Форум-топики супергруппы: провижн статических топиков под типы сообщений.

Разделение сообщений по топикам супергруппы: стопы / предупреждения / включения /
операции / дайджест. Топики создаются командой /setup_topics и хранят thread_id в
telegram_config; маршрутизация по стадии — в alert_dispatcher.

Архитектура тестируемости: оркестрация работает поверх абстрактного `TopicStore`
(Postgres-реализация + лёгкий фейк в тестах) и async-клиента Telegram. Чистые
данные (спеки топиков) — без I/O.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Палитра иконок Telegram (фиксированные 6 цветов для createForumTopic)
# ---------------------------------------------------------------------------

ICON_BLUE = 0x6FB9F0
ICON_YELLOW = 0xFFD67E
ICON_PURPLE = 0xCB86DB
ICON_GREEN = 0x8EEE98
ICON_PINK = 0xFF93B2
ICON_RED = 0xFB6F5F


@dataclass(frozen=True)
class TopicSpec:
    """Спецификация статического топика."""

    key: str  # warning / stop / enable / ops / digest
    config_column: str  # колонка telegram_config с thread_id
    name: str  # отображаемое имя топика
    icon_color: int  # цвет из палитры Telegram


# Порядок важен: создаём от самого критичного к служебному.
STATIC_TOPIC_SPECS: tuple[TopicSpec, ...] = (
    TopicSpec("stop", "forum_stop_thread_id", "🔴 Стопы", ICON_RED),
    TopicSpec("warning", "forum_warning_thread_id", "🟡 Предупреждения", ICON_YELLOW),
    TopicSpec("enable", "forum_enable_thread_id", "▶️ Включения", ICON_GREEN),
    TopicSpec("ops", "forum_ops_thread_id", "🛠 Операции", ICON_BLUE),
    TopicSpec("digest", "forum_digest_thread_id", "📊 Дайджест", ICON_PURPLE),
)

# Whitelist колонок thread_id — защита от SQL-инъекции в динамическом column name.
_ALLOWED_THREAD_COLUMNS = frozenset(spec.config_column for spec in STATIC_TOPIC_SPECS)


# ---------------------------------------------------------------------------
# Хранилище топиков (абстракция для тестируемости)
# ---------------------------------------------------------------------------


class TopicStore(Protocol):
    """Контракт доступа к thread_id топиков в telegram_config (Postgres / фейк)."""

    async def get_config_thread(self, column: str) -> int | None: ...
    async def set_config_thread(self, column: str, thread_id: int) -> None: ...


class PgTopicStore:
    """Postgres-реализация TopicStore (raw SQL поверх telegram_config)."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def get_config_thread(self, column: str) -> int | None:
        if column not in _ALLOWED_THREAD_COLUMNS:
            raise ValueError(f"Недопустимая колонка thread_id: {column}")
        async with self._engine.connect() as conn:
            row = (
                await conn.execute(
                    text(f"SELECT {column} FROM telegram_config WHERE singleton_key = 'default'")
                )
            ).first()
        return None if row is None else row[0]

    async def set_config_thread(self, column: str, thread_id: int) -> None:
        if column not in _ALLOWED_THREAD_COLUMNS:
            raise ValueError(f"Недопустимая колонка thread_id: {column}")
        async with self._engine.begin() as conn:
            await conn.execute(
                text(f"UPDATE telegram_config SET {column} = :tid WHERE singleton_key = 'default'"),
                {"tid": int(thread_id)},
            )


# ---------------------------------------------------------------------------
# Провижн (поверх TopicStore + Telegram-клиента)
# ---------------------------------------------------------------------------


def _extract_thread_id(result: Any) -> int | None:
    """Достаёт message_thread_id из ответа createForumTopic."""
    if isinstance(result, dict) and result.get("message_thread_id") is not None:
        try:
            return int(result["message_thread_id"])
        except (TypeError, ValueError):
            return None
    return None


async def provision_static_topics(
    store: TopicStore,
    client: Any,
    *,
    chat_id: int,
    specs: tuple[TopicSpec, ...] = STATIC_TOPIC_SPECS,
    force: bool = False,
) -> dict[str, dict]:
    """Идемпотентно создаёт статические топики и сохраняет thread_id в конфиг.

    Если thread_id уже задан и не force — пропускаем (status='existing').
    Ошибку создания (не форум / нет прав) не роняем — пишем status='error'.

    Возвращает отчёт {key: {'thread_id': int|None, 'status': ..., 'error'?: str}}.
    """
    report: dict[str, dict] = {}
    for spec in specs:
        existing = await store.get_config_thread(spec.config_column)
        if existing is not None and not force:
            report[spec.key] = {"thread_id": existing, "status": "existing"}
            continue
        try:
            result = await client.create_forum_topic(
                chat_id=str(chat_id), name=spec.name, icon_color=spec.icon_color
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("provision: не смог создать топик %s: %s", spec.key, exc)
            report[spec.key] = {"thread_id": None, "status": "error", "error": str(exc)}
            continue
        thread_id = _extract_thread_id(result)
        if thread_id is None:
            report[spec.key] = {
                "thread_id": None,
                "status": "error",
                "error": "no message_thread_id",
            }
            continue
        await store.set_config_thread(spec.config_column, thread_id)
        report[spec.key] = {"thread_id": thread_id, "status": "created"}
    return report


__all__ = [
    "PgTopicStore",
    "STATIC_TOPIC_SPECS",
    "TopicSpec",
    "TopicStore",
    "provision_static_topics",
]
