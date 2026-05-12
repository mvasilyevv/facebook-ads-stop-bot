"""merge_campaign_creator_and_per_offer_thresholds

Revision ID: 31684a725d7e
Revises: ef62b11fe078, d6e7f8a9b0c1
Create Date: 2026-05-11 21:51:59.326909
"""

from __future__ import annotations

from typing import Sequence

revision: str = "31684a725d7e"
down_revision: str | None = ("ef62b11fe078", "d6e7f8a9b0c1")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
