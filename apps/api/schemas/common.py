from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class DeliveryStatus(StrEnum):
    ACTIVE = "ACTIVE"
    LEARNING = "LEARNING"
    PAUSED = "PAUSED"
    NOT_DELIVERING = "NOT_DELIVERING"
    UNKNOWN = "UNKNOWN"


class TrackingMode(StrEnum):
    TRACKED = "TRACKED"
    MANUAL_BLOCK = "MANUAL_BLOCK"
    READ_ONLY = "READ_ONLY"
    ARCHIVED = "ARCHIVED"


class ScopePresence(StrEnum):
    IN_SCOPE = "IN_SCOPE"
    NOT_SEEN_THIS_SCAN = "NOT_SEEN_THIS_SCAN"
    OUT_OF_SCOPE_CONFIRMED = "OUT_OF_SCOPE_CONFIRMED"


class DecisionKind(StrEnum):
    NO_ACTION = "NO_ACTION"
    WOULD_PAUSE = "WOULD_PAUSE"
    WOULD_RESUME = "WOULD_RESUME"
    SKIPPED_BY_POLICY = "SKIPPED_BY_POLICY"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    AMBIGUOUS = "AMBIGUOUS"
    ALERT_REJECTION = "ALERT_REJECTION"
    KEPT_PAUSED_BY_VIABILITY = "KEPT_PAUSED_BY_VIABILITY"


class ExecutionState(StrEnum):
    NOT_REQUIRED = "NOT_REQUIRED"
    SKIPPED_BY_MODE = "SKIPPED_BY_MODE"
    PENDING = "PENDING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class SessionStatus(StrEnum):
    ACTIVE = "ACTIVE"
    STOPPED = "STOPPED"
    ERROR = "ERROR"


class ControlFlagTarget(StrEnum):
    CAMPAIGN = "campaign"
    ADSET = "adset"
    AD = "ad"


class HealthResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    status: str = Field(default="ok")
    service: str = Field(default="api")
    environment: str
    database_status: str
    timestamp: datetime
