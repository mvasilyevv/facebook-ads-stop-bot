"""Typed contracts for authenticated owner presentation preferences."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from core.operator.timezones import validate_iana_timezone


class OperatorDisplayPreferencePutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    timezone_name: str = Field(min_length=1, max_length=64)

    @field_validator("timezone_name")
    @classmethod
    def validate_timezone_name(cls, value: str) -> str:
        return validate_iana_timezone(value)


class OperatorDisplayPreferenceResponse(BaseModel):
    timezone_name: str
    updated_at: datetime


__all__ = [
    "OperatorDisplayPreferencePutRequest",
    "OperatorDisplayPreferenceResponse",
]
