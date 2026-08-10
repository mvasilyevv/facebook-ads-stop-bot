"""Static and validation contracts for owner display-timezone preferences."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from apps.api.routers.v1.schemas.operator_preferences import (
    OperatorDisplayPreferencePutRequest,
)
from core.models import Base
from core.operator.display_preferences import (
    DEFAULT_OPERATOR_DISPLAY_TIMEZONE,
    validate_iana_timezone,
)


def test_iana_timezone_validation_is_backend_authoritative() -> None:
    assert validate_iana_timezone(" Europe/Kaliningrad ") == "Europe/Kaliningrad"
    assert validate_iana_timezone("UTC") == "UTC"
    assert DEFAULT_OPERATOR_DISPLAY_TIMEZONE == "Europe/Kaliningrad"
    for invalid in ("", "Mars/Olympus", "../UTC"):
        with pytest.raises(ValueError, match="unknown IANA timezone"):
            validate_iana_timezone(invalid)


def test_put_contract_rejects_unknown_timezone_and_extra_fields() -> None:
    assert (
        OperatorDisplayPreferencePutRequest(timezone_name=" Europe/Moscow ").timezone_name
        == "Europe/Moscow"
    )
    with pytest.raises(ValidationError):
        OperatorDisplayPreferencePutRequest(timezone_name="GMT+03:00")
    with pytest.raises(ValidationError):
        OperatorDisplayPreferencePutRequest(
            timezone_name="UTC",
            device_timezone="Europe/London",  # type: ignore[call-arg]
        )


def test_model_is_normalized_owner_bound_and_has_no_runtime_blob() -> None:
    table = Base.metadata.tables["operator_display_preferences"]

    assert set(table.columns) == {
        table.c.owner_recipient_id,
        table.c.timezone_name,
        table.c.created_at,
        table.c.updated_at,
    }
    assert list(table.primary_key.columns) == [table.c.owner_recipient_id]
    assert table.c.timezone_name.nullable is False
    assert table.c.timezone_name.type.length == 64
    assert table.c.owner_recipient_id.foreign_keys
    foreign_key = next(iter(table.c.owner_recipient_id.foreign_keys))
    assert foreign_key.target_fullname == "telegram_recipients.id"
    assert foreign_key.ondelete == "CASCADE"
