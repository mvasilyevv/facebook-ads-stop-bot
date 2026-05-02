# -*- coding: utf-8 -*-
"""Тесты функции resolve_thread_id: маппинг stream → forum topic thread_id."""

from __future__ import annotations

from types import SimpleNamespace


def _make_settings(**kwargs) -> object:
    """Создаёт заглушку TelegramSettings с заданными атрибутами."""
    defaults = {
        "forum_topics_enabled": True,
        "topic_alerts_thread_id": 101,
        "topic_disabled_thread_id": 202,
        "topic_recommendations_thread_id": 303,
        "topic_ops_thread_id": 404,
        "topic_logs_thread_id": 505,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


# Проверяем, что stream "alert" возвращает topic_alerts_thread_id.
def test_resolve_thread_id_alert():
    from core.telegram.delivery import resolve_thread_id

    settings = _make_settings()
    assert resolve_thread_id("alert", settings) == 101


# Проверяем, что stream "disabled" возвращает topic_disabled_thread_id.
def test_resolve_thread_id_disabled():
    from core.telegram.delivery import resolve_thread_id

    settings = _make_settings()
    assert resolve_thread_id("disabled", settings) == 202


# Проверяем, что stream "recommendation" возвращает topic_recommendations_thread_id.
def test_resolve_thread_id_recommendation():
    from core.telegram.delivery import resolve_thread_id

    settings = _make_settings()
    assert resolve_thread_id("recommendation", settings) == 303


# Проверяем, что stream "ops" возвращает topic_ops_thread_id.
def test_resolve_thread_id_ops():
    from core.telegram.delivery import resolve_thread_id

    settings = _make_settings()
    assert resolve_thread_id("ops", settings) == 404


# Проверяем, что stream "logs" возвращает topic_logs_thread_id.
def test_resolve_thread_id_logs():
    from core.telegram.delivery import resolve_thread_id

    settings = _make_settings()
    assert resolve_thread_id("logs", settings) == 505


# Проверяем, что при forum_topics_enabled=False все streams возвращают None.
def test_resolve_thread_id_disabled_flag_returns_none_for_all():
    from core.telegram.delivery import resolve_thread_id

    settings = _make_settings(forum_topics_enabled=False)
    for stream in ("alert", "disabled", "recommendation", "ops", "logs"):
        assert resolve_thread_id(stream, settings) is None, (
            f"Ожидался None для stream='{stream}' при forum_topics_enabled=False"
        )


# Проверяем, что неизвестный stream возвращает None даже при включённых topics.
def test_resolve_thread_id_unknown_stream_returns_none():
    from core.telegram.delivery import resolve_thread_id

    settings = _make_settings()
    assert resolve_thread_id("unknown_stream", settings) is None
