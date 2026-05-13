"""Сборка markdown-отчёта по списку UserAction."""

from __future__ import annotations

from datetime import datetime

from core.campaign_recorder.analyzer import UserAction


def _format_duration(seconds: float) -> str:
    s = max(0, int(seconds))
    if s < 60:
        return f"{s} сек"
    return f"{s // 60} мин {s % 60} сек"


def _format_started(session: dict) -> str:
    started = session.get("started_at")
    if isinstance(started, str) and started:
        try:
            dt = datetime.fromisoformat(started)
            return dt.strftime("%Y-%m-%d %H:%M")
        except ValueError:
            pass
    if isinstance(started, (int, float)):
        try:
            return datetime.fromtimestamp(float(started)).strftime("%Y-%m-%d %H:%M")
        except (OSError, ValueError):
            pass
    events = session.get("events") or []
    if events:
        try:
            return datetime.fromtimestamp(float(events[0].get("ts") or 0)).strftime(
                "%Y-%m-%d %H:%M"
            )
        except (OSError, ValueError, TypeError):
            pass
    return "—"


def _duration_seconds(session: dict) -> float:
    events = session.get("events") or []
    if not events:
        return 0.0
    try:
        first = float(events[0].get("ts") or 0)
        last = float(events[-1].get("ts") or 0)
        return max(0.0, last - first)
    except (TypeError, ValueError):
        return 0.0


def _source(session: dict) -> str:
    return str(session.get("path") or session.get("filename") or "—")


def _action_block(idx: int, action: UserAction) -> str:
    lines: list[str] = [f"## Шаг {idx} — {action.kind}", ""]
    if action.kind == "fill":
        what = f"поле «{action.label}»" if action.label else "поле"
    elif action.kind == "select":
        what = f"список «{action.label}»" if action.label else "список"
    elif action.kind == "key":
        what = f"клавиша «{action.label}»" if action.label else "клавиша"
    else:
        what = f"«{action.label}»" if action.label else "—"
    lines.append(f"**Что:** {what}")

    widget = action.widget or {}
    if widget and not widget.get("is_self"):
        # Семантический контекст: клик внутри какого виджета сделан.
        role = widget.get("role")
        wname = (
            widget.get("name")
            or widget.get("aria_label")
            or widget.get("labelled_by")
            or widget.get("inner_text")
        )
        if role and wname:
            lines.append(f"**Виджет:** `role={role}` «{wname}»")
        elif role:
            lines.append(f"**Виджет:** `role={role}`")

    if action.opened_after:
        # Что появилось на экране после клика — раскрылся ли диалог/меню.
        preview = ", ".join(action.opened_after[:6])
        lines.append(f"**Открылось после клика:** {preview}")

    if action.section:
        lines.append(f"**Секция:** {action.section}")

    if action.kind in ("fill", "select", "key") and action.value is not None:
        lines.append(f"**Значение:** `{action.value}`")

    if action.selectors:
        lines.append("")
        lines.append("Селекторы:")
        for n, sel in enumerate(action.selectors, start=1):
            lines.append(f"{n}. `{sel}`")

    return "\n".join(lines)


def build_markdown(session: dict, actions: list[UserAction]) -> str:
    offer = session.get("offer_code") or "—"
    started = _format_started(session)
    raw = len(session.get("events") or [])
    n = len(actions)
    duration = _format_duration(_duration_seconds(session))

    parts: list[str] = [
        f"# Запись {offer} — {started} — {n} действий",
        "",
        f"Источник: `{_source(session)}`",
        f"Длительность: {duration}",
        f"Сырых событий: {raw} → действий: {n}",
        "",
        "---",
        "",
    ]

    for idx, action in enumerate(actions, start=1):
        parts.append(_action_block(idx, action))
        parts.append("")
        parts.append("---")
        parts.append("")

    return "\n".join(parts).rstrip() + "\n"
