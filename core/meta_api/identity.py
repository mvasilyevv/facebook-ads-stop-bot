"""Canonical, explicit Meta ad-account identity.

Money commands and scanner writes must never infer an account from whichever
browser tab happens to be focused.  The canonical in-process/database form is
the numeric account id without Meta's ``act_`` presentation prefix.
"""

from __future__ import annotations

from typing import Any


def require_ad_account_id(value: Any, *, field_name: str = "ad_account_id") -> str:
    """Return a canonical numeric account id or reject the request.

    ``act_123`` is accepted at API boundaries and normalized to ``123``.  Empty,
    non-numeric and boolean values are rejected; there is deliberately no
    browser-tab or singleton-account fallback.
    """
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError(f"{field_name} must be an explicit numeric account id")
    normalized = str(value).strip().removeprefix("act_").strip()
    if not normalized or not normalized.isdigit():
        raise ValueError(f"{field_name} must be an explicit numeric account id")
    return normalized


def graph_ad_account_id(value: Any, *, field_name: str = "ad_account_id") -> str:
    """Return Meta's ``act_<id>`` form for an already explicit account."""
    return f"act_{require_ad_account_id(value, field_name=field_name)}"


__all__ = ["graph_ad_account_id", "require_ad_account_id"]
