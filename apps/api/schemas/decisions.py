from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from apps.api.schemas.common import DecisionKind


class DecisionItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    scan_run_id: str
    fb_ad_id: str
    rule_id: str | None
    decision: DecisionKind
    reason: str
    action_executed: bool
    action_status: str | None
    resolved_cpa_usd: Decimal | None = None
    created_at: datetime
