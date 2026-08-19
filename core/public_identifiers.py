from __future__ import annotations

import base64
import uuid


def public_uuid(value: object, *, prefix: str) -> str:
    """Encode an internal UUID as a stable opaque public identifier."""

    identifier = value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    token = base64.urlsafe_b64encode(identifier.bytes).decode("ascii").rstrip("=")
    return f"{prefix}_{token}"


def parse_public_uuid(value: object, *, prefix: str) -> uuid.UUID:
    """Decode an opaque public identifier; UUID objects are accepted for internal callers."""

    if isinstance(value, uuid.UUID):
        return value
    text = str(value)
    marker = f"{prefix}_"
    if not text.startswith(marker):
        raise ValueError("invalid public identifier")
    token = text.removeprefix(marker)
    if len(token) != 22:
        raise ValueError("invalid public identifier")
    try:
        raw = base64.b64decode(token + "==", altchars=b"-_", validate=True)
        return uuid.UUID(bytes=raw)
    except (ValueError, TypeError) as exc:
        raise ValueError("invalid public identifier") from exc


__all__ = ["parse_public_uuid", "public_uuid"]
