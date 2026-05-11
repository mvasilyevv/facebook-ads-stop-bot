from __future__ import annotations

import json
import logging
from collections import Counter
from pathlib import Path

logger = logging.getLogger(__name__)


def _selector_for_event(event: dict) -> str | None:
    if event.get("id"):
        return f"#{event['id']}"
    if event.get("aria_label"):
        return f"[aria-label=\"{event['aria_label']}\"]"
    data = event.get("data_attrs", {})
    if data:
        key, val = next(iter(data.items()))
        return f"[{key}=\"{val}\"]"
    return None


def _is_stable(event: dict) -> bool:
    return bool(event.get("id") or event.get("aria_label") or event.get("data_attrs"))


def analyze_session(session: dict) -> dict:
    """Анализирует сессию и возвращает отчёт с паттернами."""
    events: list[dict] = session.get("events", [])
    by_type: Counter = Counter(e.get("type") for e in events)

    stable: list[dict] = []
    fragile: list[dict] = []
    for event in events:
        selector = _selector_for_event(event)
        entry = {
            "selector": selector or event.get("xpath", ""),
            "type": event.get("type"),
            "tag": event.get("tag"),
            "text": event.get("text", "")[:80],
            "value": event.get("value"),
            "is_stable": _is_stable(event),
        }
        if _is_stable(event) and selector:
            stable.append(entry)
        else:
            fragile.append(entry)

    steps = [
        {
            "step": i + 1,
            "type": e.get("type"),
            "text": (e.get("text") or "")[:60],
            "value": e.get("value"),
        }
        for i, e in enumerate(events)
        if e.get("type") in ("click", "input", "change")
    ]

    return {
        "offer_code": session.get("offer_code", ""),
        "total_events": len(events),
        "by_type": dict(by_type),
        "stable_selectors": stable,
        "fragile_selectors": fragile,
        "steps_summary": steps,
        "recommendations": _build_recommendations(stable, fragile),
    }


def _build_recommendations(stable: list, fragile: list) -> list[str]:
    recs = []
    if fragile:
        recs.append(
            f"{len(fragile)} элементов без стабильного селектора — "
            "возможны проблемы при автоматизации. Используй aria-label или data-атрибуты."
        )
    if stable:
        recs.append(
            f"{len(stable)} элементов имеют надёжные селекторы — готовы к автоматизации."
        )
    if not recs:
        recs.append("Недостаточно данных для анализа.")
    return recs


def analyze_session_file(path: Path) -> dict:
    """Загружает JSON-файл сессии и возвращает отчёт."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return analyze_session(data)
