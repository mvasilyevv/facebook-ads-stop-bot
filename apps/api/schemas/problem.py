"""Canonical error contract shared by every HTTP endpoint."""

from __future__ import annotations

from pydantic import BaseModel


class ApiProblem(BaseModel):
    """Stable, non-secret error envelope returned by the HTTP API."""

    code: str
    message: str
    correlation_id: str
    field_errors: dict[str, list[str]] | None


__all__ = ["ApiProblem"]
