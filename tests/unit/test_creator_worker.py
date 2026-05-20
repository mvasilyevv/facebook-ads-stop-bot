# Тесты creator_worker: маппинг PlanEvent в step_log + сценарии финализации
# выполняются с in-memory SQLite (через override get_session_factory).
from __future__ import annotations

from types import SimpleNamespace

from apps.creator_worker.main import _event_to_log_entry


def _make_event(field: str, payload: dict) -> SimpleNamespace:
    """Эмулирует PlanEvent oneof через SimpleNamespace с HasField/getattr."""
    fields = {
        "started",
        "finished",
        "failed",
        "skipped",
        "checkpoint",
        "complete",
    }
    inner = SimpleNamespace(**payload)
    obj = SimpleNamespace(**{name: SimpleNamespace() for name in fields})
    setattr(obj, field, inner)
    obj.HasField = lambda name, _f=field: name == _f  # type: ignore[attr-defined]
    return obj


# Сценарий: started → корректные ключи в step_log.
def test_event_to_log_entry_started():
    event = _make_event("started", {"step": "open_page", "index": 0, "timestamp_ms": 123})
    entry = _event_to_log_entry(event)
    assert entry["event"] == "step_started"
    assert entry["step"] == "open_page"
    assert entry["index"] == 0
    assert entry["timestamp_ms"] == 123
    assert "logged_at" in entry


# Сценарий: finished с валидным JSON detail → раскладывается в dict.
def test_event_to_log_entry_finished_with_detail_json():
    event = _make_event(
        "finished",
        {
            "step": "fill_texts",
            "index": 5,
            "timestamp_ms": 999,
            "detail_json": '{"applied":true,"count":3}',
        },
    )
    entry = _event_to_log_entry(event)
    assert entry["event"] == "step_finished"
    assert entry["detail"] == {"applied": True, "count": 3}


# Сценарий: finished с битым JSON detail → попадает в detail_raw.
def test_event_to_log_entry_finished_with_broken_detail():
    event = _make_event(
        "finished",
        {
            "step": "x",
            "index": 1,
            "timestamp_ms": 1,
            "detail_json": "{not json",
        },
    )
    entry = _event_to_log_entry(event)
    assert entry["detail_raw"] == "{not json"
    assert "detail" not in entry


# Сценарий: failed → попадает error.
def test_event_to_log_entry_failed():
    event = _make_event(
        "failed",
        {
            "step": "set_geo",
            "index": 2,
            "error": "timeout",
            "timestamp_ms": 7,
        },
    )
    entry = _event_to_log_entry(event)
    assert entry["event"] == "step_failed"
    assert entry["error"] == "timeout"


# Сценарий: skipped → попадает reason.
def test_event_to_log_entry_skipped():
    event = _make_event(
        "skipped",
        {
            "step": "set_age",
            "index": 3,
            "reason": "уже установлено",
            "timestamp_ms": 8,
        },
    )
    entry = _event_to_log_entry(event)
    assert entry["event"] == "step_skipped"
    assert entry["reason"] == "уже установлено"


# Сценарий: checkpoint → url + detail.
def test_event_to_log_entry_checkpoint():
    event = _make_event(
        "checkpoint",
        {
            "url": "https://facebook.com/checkpoint/123",
            "detail": "FB checkpoint detected",
        },
    )
    entry = _event_to_log_entry(event)
    assert entry["event"] == "checkpoint"
    assert entry["url"] == "https://facebook.com/checkpoint/123"
    assert entry["detail"] == "FB checkpoint detected"


# Сценарий: complete ok → ok/total_steps/duration_ms.
def test_event_to_log_entry_complete_ok():
    event = _make_event(
        "complete",
        {
            "ok": True,
            "error": "",
            "total_steps": 12,
            "duration_ms": 4567,
        },
    )
    entry = _event_to_log_entry(event)
    assert entry["event"] == "complete"
    assert entry["ok"] is True
    assert entry["total_steps"] == 12
    assert entry["duration_ms"] == 4567


# Сценарий: complete fail → передаёт error.
def test_event_to_log_entry_complete_fail():
    event = _make_event(
        "complete",
        {
            "ok": False,
            "error": "step failed",
            "total_steps": 5,
            "duration_ms": 100,
        },
    )
    entry = _event_to_log_entry(event)
    assert entry["ok"] is False
    assert entry["error"] == "step failed"


# Сценарий: неизвестный oneof → None.
def test_event_to_log_entry_unknown_returns_none():
    obj = SimpleNamespace()
    obj.HasField = lambda _name: False  # type: ignore[attr-defined]
    assert _event_to_log_entry(obj) is None
