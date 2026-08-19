# -*- coding: utf-8 -*-
"""Short, deterministic HTML renderer for operator Telegram cards."""

from __future__ import annotations

import hashlib
import html
import json
import re
from dataclasses import dataclass
from typing import Mapping
from urllib.parse import parse_qsl, urlsplit

from core.safe_diagnostics import redact_sensitive_text
from core.telegram.schemas import NotificationCardFacts, NotificationEventSpec

_MAX_CARD_CHARS = 700
_ACTION_CALLBACK_RE = re.compile(r"^a:[A-Za-z0-9_-]{22}$")
_RAW_CAPABILITY_RE = re.compile(r"^[A-Za-z0-9_-]{22}$")
_SEVERITY_MARK = {
    "ok": "✅",
    "warning": "⚠️",
    "critical": "🛑",
    "unknown": "❔",
}


@dataclass(frozen=True)
class RenderedNotification:
    text: str
    reply_markup: dict | None
    render_hash: bytes


def _safe_line(value: str, *, max_length: int) -> str:
    redacted = redact_sensitive_text(value).strip()
    escaped: list[str] = []
    used = 0
    truncated = False
    for char in redacted:
        fragment = html.escape(char, quote=False)
        if used + len(fragment) > max_length:
            truncated = True
            break
        escaped.append(fragment)
        used += len(fragment)
    if truncated:
        while escaped and used + 1 > max_length:
            used -= len(escaped.pop())
        escaped.append("…")
    return "".join(escaped)


def _safe_button_label(value: str) -> str:
    return redact_sensitive_text(value).strip()[:64] or "Действие"


def _is_safe_navigation_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        return False
    return bool(
        parsed.scheme == "https"
        and parsed.hostname
        and parsed.username is None
        and parsed.password is None
        and not parsed.fragment
        and not any(char.isspace() for char in value)
        and len(query) == 1
        and query[0][0] in {"nav", "startapp"}
        and _RAW_CAPABILITY_RE.fullmatch(query[0][1])
    )


def _render_lines(facts: NotificationCardFacts, severity: str) -> list[str]:
    mark = _SEVERITY_MARK.get(severity, "ℹ️")
    lines = [f"<b>{mark} {_safe_line(facts.title, max_length=200)}</b>"]

    data_lines: list[str] = []
    if facts.summary:
        data_lines.append(_safe_line(facts.summary, max_length=280))
    data_lines.extend(_safe_line(item, max_length=180) for item in facts.lines[:5])
    if facts.risk:
        data_lines.append(f"Риск: {_safe_line(facts.risk, max_length=170)}")
    if facts.status:
        data_lines.append(f"Статус: {_safe_line(facts.status, max_length=70)}")

    lines.extend(data_lines[:5])
    while len("\n".join(lines)) > _MAX_CARD_CHARS and len(lines) > 1:
        lines.pop()
    return lines


def render_notification(
    event: NotificationEventSpec,
    *,
    action_callbacks: Mapping[str, str] | None = None,
    navigation_url: str | None = None,
) -> RenderedNotification:
    """Render an HTML-only card with at most two deterministic buttons."""
    action_callbacks = action_callbacks or {}
    text = "\n".join(_render_lines(event.facts, event.severity))
    if len(text) > _MAX_CARD_CHARS:  # defensive; constrained facts normally prevent this
        raise ValueError("Rendered Telegram card exceeds 700 characters")

    buttons: list[dict[str, object]] = []
    if navigation_url:
        if not _is_safe_navigation_url(navigation_url):
            raise ValueError("Invalid opaque Telegram navigation URL")
        buttons.append({"text": "Открыть", "url": navigation_url})
    for action in event.actions:
        callback_data = action_callbacks.get(action.key)
        if callback_data:
            if not _ACTION_CALLBACK_RE.fullmatch(callback_data):
                raise ValueError("Invalid opaque Telegram callback_data")
            buttons.append(
                {"text": _safe_button_label(action.label), "callback_data": callback_data}
            )
        if len(buttons) == 2:
            break

    reply_markup = {"inline_keyboard": [[button] for button in buttons]} if buttons else None
    canonical = json.dumps(
        {"text": text, "reply_markup": reply_markup},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return RenderedNotification(
        text=text,
        reply_markup=reply_markup,
        render_hash=hashlib.sha256(canonical).digest(),
    )


__all__ = ["RenderedNotification", "render_notification"]
