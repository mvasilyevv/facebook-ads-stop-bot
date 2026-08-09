"""Pure IANA timezone policy shared by operator API and adoption contracts."""

from __future__ import annotations

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_OPERATOR_DISPLAY_TIMEZONE = "Europe/Kaliningrad"


def validate_iana_timezone(value: str) -> str:
    """Return a trimmed IANA timezone or raise without guessing a substitute."""

    timezone_name = value.strip()
    if not timezone_name or len(timezone_name) > 64:
        raise ValueError("unknown IANA timezone")
    try:
        ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("unknown IANA timezone") from exc
    return timezone_name


__all__ = ["DEFAULT_OPERATOR_DISPLAY_TIMEZONE", "validate_iana_timezone"]
