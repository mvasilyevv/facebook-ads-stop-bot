# -*- coding: utf-8 -*-
"""Small, Bot API-compatible HTML helpers for command replies."""

from __future__ import annotations

import html
from typing import Any


def esc(value: Any) -> str:
    """Escape a dynamic value for Telegram ``parse_mode=HTML``."""
    return html.escape("" if value is None else str(value), quote=False)


def code(value: Any) -> str:
    """Render an escaped inline-code fragment supported by Telegram."""
    return f"<code>{esc(value)}</code>"


__all__ = ["code", "esc"]
