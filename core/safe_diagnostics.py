# -*- coding: utf-8 -*-
"""Bounded diagnostics and public-text redaction for untrusted failures."""

from __future__ import annotations

import re
from enum import Enum

_SAFE_DIAGNOSTIC_VALUE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,31}$")
_UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
_TELEGRAM_BOT_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_-])\d{5,}:[A-Za-z0-9_-]{20,}")
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}")
# Graph-токен в тексте ответа Meta приходит и без имени поля («Session for EAA… is
# invalid»), поэтому _NAMED_SECRET_RE его не ловит.
_META_ACCESS_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_-])EAA[A-Za-z0-9_-]{16,}")
_NAMED_SECRET_RE = re.compile(
    r"(?i)\b(?P<name>access_token|api[_-]?key|x-token|token|password|secret)"
    r"(?P<separator>\s*[:=]\s*)[^\s&;,]+"
)
_CAPABILITY_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9_-])(?P<prefix>a:|(?:nav|startapp)=)[A-Za-z0-9_-]{22}"
)
_URL_QUERY_RE = re.compile(r"(?P<base>https?://[^\s?#]+)\?[^\s#]+", re.IGNORECASE)


def _diagnostic_scalar(value: object) -> str | None:
    if isinstance(value, Enum):
        value = value.value
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str) and _SAFE_DIAGNOSTIC_VALUE.fullmatch(value):
        return value
    return None


def safe_exception_diagnostic(exc: BaseException) -> str:
    """Return exception type and bounded machine codes without calling ``str(exc)``."""

    error_type = type(exc).__name__
    if not error_type.isidentifier() or len(error_type) > 128:
        error_type = "Exception"
    fields = [f"error_type={error_type}"]
    for attribute, label in (
        ("code", "code"),
        ("subcode", "subcode"),
        ("status_code", "status"),
        ("kind", "kind"),
    ):
        try:
            value = getattr(exc, attribute, None)
        except Exception:  # noqa: BLE001 - hostile exception properties are ignored
            continue
        if callable(value):
            continue
        scalar = _diagnostic_scalar(value)
        if scalar is not None:
            fields.append(f"{label}={scalar}")
    return " ".join(fields)


def redact_sensitive_text(value: object) -> str:
    """Redact common credentials, capabilities, UUIDs and URL query contents."""

    text = str(value or "")
    text = _URL_QUERY_RE.sub(r"\g<base>?<redacted>", text)
    text = _BEARER_RE.sub("Bearer <redacted>", text)
    text = _META_ACCESS_TOKEN_RE.sub("<redacted>", text)
    text = _TELEGRAM_BOT_TOKEN_RE.sub("<redacted>", text)
    text = _NAMED_SECRET_RE.sub(
        lambda match: f"{match.group('name')}{match.group('separator')}<redacted>",
        text,
    )
    text = _CAPABILITY_RE.sub(
        lambda match: f"{match.group('prefix')}<redacted>",
        text,
    )
    return _UUID_RE.sub("объект", text)


__all__ = ["redact_sensitive_text", "safe_exception_diagnostic"]
