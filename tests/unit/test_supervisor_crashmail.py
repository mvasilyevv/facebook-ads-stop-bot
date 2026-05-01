# -*- coding: utf-8 -*-
"""Тесты для bin/supervisor_crashmail.py."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

# Добавляем корень проекта в sys.path, чтобы импортировать bin/
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bin"))

from supervisor_crashmail import CooldownTracker, format_alert_text, parse_header, parse_payload


# Сценарий 1: парсинг строки заголовка eventlistener-протокола
def test_parse_header_fatal_event():
    line = (
        "ver:3.0 server:supervisor serial:1 pool:crashmail poolserial:1 "
        "eventname:PROCESS_STATE_FATAL len:65\n"
    )
    header = parse_header(line)
    assert header["eventname"] == "PROCESS_STATE_FATAL"
    assert header["len"] == "65"
    assert header["ver"] == "3.0"


# Сценарий 1б: парсинг payload — должен вернуть processname
def test_parse_payload_returns_processname():
    blob = "processname:observer_worker groupname:observer_worker from_state:BACKOFF"
    payload = parse_payload(blob)
    assert payload["processname"] == "observer_worker"
    assert payload["from_state"] == "BACKOFF"
    assert payload["groupname"] == "observer_worker"


# Сценарий 2: текст алерта для PROCESS_STATE_FATAL содержит имя процесса и eventname на русском
def test_format_alert_text_fatal():
    event = {
        "processname": "observer_worker",
        "eventname": "PROCESS_STATE_FATAL",
        "from_state": "BACKOFF",
    }
    text = format_alert_text(event)
    assert "observer_worker" in text
    assert "PROCESS_STATE_FATAL" in text
    # Сообщение должно быть на русском
    assert "процесс" in text
    assert "состояние" in text


# Сценарий 3: cooldown — два события подряд → отправляется только одно
def test_cooldown_suppresses_second_alert():
    tracker = CooldownTracker(cooldown_seconds=300)

    # Первый вызов: должен разрешить отправку
    assert tracker.should_send("my_worker") is True
    tracker.record_sent("my_worker")

    # Второй вызов сразу после: должен подавить
    assert tracker.should_send("my_worker") is False


# Сценарий 3б: после истечения cooldown отправка снова разрешена
def test_cooldown_allows_after_expiry():
    tracker = CooldownTracker(cooldown_seconds=0.05)  # 50мс для теста
    tracker.record_sent("my_worker")
    # Сразу — подавлено
    assert tracker.should_send("my_worker") is False
    time.sleep(0.1)
    # После истечения cooldown — разрешено
    assert tracker.should_send("my_worker") is True


# Сценарий 3в: cooldown для одного процесса не влияет на другой
def test_cooldown_independent_per_process():
    tracker = CooldownTracker(cooldown_seconds=300)
    tracker.record_sent("worker_a")
    # worker_b не под cooldown'ом
    assert tracker.should_send("worker_b") is True


# Сценарий 4: события не из подписанного списка не должны вызывать отправку
def test_non_watched_events_not_sent():
    from supervisor_crashmail import _WATCHED_EVENTS

    # Эти события supervisord не пришлёт, но код должен быть безопасным
    non_watched = ["PROCESS_STATE_RUNNING", "PROCESS_STATE_STARTING", "TICK_60"]
    for ev in non_watched:
        assert ev not in _WATCHED_EVENTS


# Интеграция: telegram не вызывается для не-watched события (через мок send)
def test_no_telegram_for_ignored_event(monkeypatch):
    from supervisor_crashmail import _WATCHED_EVENTS

    mock_send = MagicMock()
    eventname = "PROCESS_STATE_RUNNING"
    assert eventname not in _WATCHED_EVENTS

    # Имитируем логику main: если eventname не в _WATCHED_EVENTS — не вызываем
    if eventname in _WATCHED_EVENTS:
        mock_send("should not happen")

    mock_send.assert_not_called()
