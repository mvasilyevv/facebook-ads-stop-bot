# -*- coding: utf-8 -*-
"""Public contracts for launching the protected Vision desktop."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class DesktopLaunchResponse(BaseModel):
    """A short-lived, single-use URL that establishes a desktop session."""

    url: str
    expires_at: datetime


__all__ = ["DesktopLaunchResponse"]
