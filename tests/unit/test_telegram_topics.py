# -*- coding: utf-8 -*-
"""Unit-тесты core.telegram.topics — провижн статических топиков на фейках.

Без БД и без реального Telegram: TopicStore и клиент заменены in-memory фейками,
проверяется именно логика провижна.
"""

from __future__ import annotations

import pytest

from core.telegram import topics as T
from core.telegram.topics import STATIC_TOPIC_SPECS, provision_static_topics


class FakeClient:
    """Фейковый TG-клиент: выдаёт инкрементные thread_id, считает вызовы."""

    def __init__(self, *, fail_create: bool = False) -> None:
        self._next = 100
        self.fail_create = fail_create
        self.created: list[dict] = []

    async def create_forum_topic(self, *, chat_id: str, name: str, icon_color: int) -> dict:
        if self.fail_create:
            raise RuntimeError("not a forum / no can_manage_topics")
        self._next += 1
        self.created.append({"name": name, "icon_color": icon_color, "thread_id": self._next})
        return {"message_thread_id": self._next, "name": name}


class FakeStore:
    """In-memory TopicStore (только thread_id колонки конфига)."""

    def __init__(self) -> None:
        self.config: dict[str, int | None] = {}

    async def get_config_thread(self, column: str) -> int | None:
        return self.config.get(column)

    async def set_config_thread(self, column: str, thread_id: int) -> None:
        self.config[column] = thread_id


# Спеки: 5 статических топиков, уникальные колонки, цвета из палитры Telegram
def test_static_specs_sane() -> None:
    keys = [s.key for s in STATIC_TOPIC_SPECS]
    assert keys == ["stop", "warning", "enable", "ops", "digest"]
    cols = {s.config_column for s in STATIC_TOPIC_SPECS}
    assert len(cols) == 5
    palette = {
        T.ICON_BLUE,
        T.ICON_YELLOW,
        T.ICON_PURPLE,
        T.ICON_GREEN,
        T.ICON_PINK,
        T.ICON_RED,
    }
    assert all(s.icon_color in palette for s in STATIC_TOPIC_SPECS)


# Пустой конфиг → создаёт все 5 топиков, сохраняет thread_id, status=created
@pytest.mark.asyncio
async def test_provision_creates_all() -> None:
    store, client = FakeStore(), FakeClient()
    report = await provision_static_topics(store, client, chat_id=-100123)
    assert len(client.created) == 5
    assert all(r["status"] == "created" for r in report.values())
    assert store.config["forum_stop_thread_id"] == report["stop"]["thread_id"]
    assert store.config["forum_digest_thread_id"] == report["digest"]["thread_id"]


# Идемпотентность: уже заданный thread_id не пересоздаётся
@pytest.mark.asyncio
async def test_provision_idempotent() -> None:
    store, client = FakeStore(), FakeClient()
    store.config["forum_stop_thread_id"] = 55  # уже есть
    report = await provision_static_topics(store, client, chat_id=-100123)
    assert report["stop"] == {"thread_id": 55, "status": "existing"}
    assert len(client.created) == 4  # создались только 4 оставшихся


# force=True пересоздаёт даже существующие
@pytest.mark.asyncio
async def test_provision_force_recreates() -> None:
    store, client = FakeStore(), FakeClient()
    store.config["forum_stop_thread_id"] = 55
    report = await provision_static_topics(store, client, chat_id=-100123, force=True)
    assert report["stop"]["status"] == "created"
    assert len(client.created) == 5


# Ошибка создания (не форум/нет прав) → status=error, не падаем, конфиг не трогаем
@pytest.mark.asyncio
async def test_provision_handles_create_error() -> None:
    store, client = FakeStore(), FakeClient(fail_create=True)
    report = await provision_static_topics(store, client, chat_id=-100123)
    assert all(r["status"] == "error" for r in report.values())
    assert store.config == {}  # ничего не записали
