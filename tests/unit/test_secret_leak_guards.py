from __future__ import annotations

import uuid

import pytest

from core.public_identifiers import parse_public_uuid, public_uuid
from core.safe_diagnostics import redact_sensitive_text, safe_exception_diagnostic

_SECRET = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef"
_UUID = "00000000-0000-4000-8000-000000000099"


class _CodedSecretError(RuntimeError):
    code = 190
    subcode = 463


def test_safe_diagnostic_never_formats_exception_message_or_traceback() -> None:
    exc = _CodedSecretError(
        f"access_token={_SECRET} https://tracker.test/cb?token={_SECRET} {_UUID}"
    )

    diagnostic = safe_exception_diagnostic(exc)

    assert diagnostic == "error_type=_CodedSecretError code=190 subcode=463"
    assert _SECRET not in diagnostic
    assert _UUID not in diagnostic
    assert "Traceback" not in diagnostic


def test_public_text_redactor_covers_credentials_capabilities_queries_and_uuid() -> None:
    value = (
        f"Bearer {_SECRET} token={_SECRET} {_SECRET} a:{'A' * 22} "
        f"https://tracker.test/cb?email=user@example.test&token={_SECRET} {_UUID}"
    )

    redacted = redact_sensitive_text(value)

    assert _SECRET not in redacted
    assert _UUID not in redacted
    assert "user@example.test" not in redacted
    assert "A" * 22 not in redacted
    assert "<redacted>" in redacted
    assert "объект" in redacted


def test_public_uuid_is_opaque_round_trip_and_raw_uuid_is_not_accepted_as_public() -> None:
    internal = uuid.UUID(_UUID)

    public = public_uuid(internal, prefix="inc")

    assert public.startswith("inc_")
    assert _UUID not in public
    assert parse_public_uuid(public, prefix="inc") == internal
    with pytest.raises(ValueError, match="invalid public identifier"):
        parse_public_uuid(_UUID, prefix="inc")
