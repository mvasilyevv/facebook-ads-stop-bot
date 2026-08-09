# -*- coding: utf-8 -*-
"""Public contracts for launching the protected Vision desktop."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class DesktopLaunchRequest(BaseModel):
    """Platform-selected presentation profile carried into the desktop session."""

    presentation: Literal["desktop", "mobile"]


class DesktopLaunchResponse(BaseModel):
    """A short-lived, single-use URL that establishes a desktop session."""

    url: str
    expires_at: datetime
    transport: Literal["kasm"]


class DesktopTransportsResponse(BaseModel):
    """Configured transport selection exposed to owner launchers."""

    active: Literal["kasm"]
    available: list[Literal["kasm"]]


__all__ = ["DesktopLaunchRequest", "DesktopLaunchResponse", "DesktopTransportsResponse"]
