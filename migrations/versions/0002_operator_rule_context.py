# -*- coding: utf-8 -*-
"""Persist evaluator-owned nearest STOP context on ads.

The revision is additive, repeat-safe and forward-only. Existing rows remain
unknown until their next observer scan writes a complete evaluator projection.
"""

from __future__ import annotations

from alembic import op

revision = "0002_operator_rule_context"
down_revision = "0001_safety_first_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE public.fb_ads ADD COLUMN IF NOT EXISTS matched_offer_code VARCHAR(32)")
    op.execute("ALTER TABLE public.fb_ads ADD COLUMN IF NOT EXISTS nearest_rule_code VARCHAR(64)")
    op.execute(
        "ALTER TABLE public.fb_ads ADD COLUMN IF NOT EXISTS nearest_rule_value NUMERIC(20, 6)"
    )
    op.execute(
        "ALTER TABLE public.fb_ads ADD COLUMN IF NOT EXISTS nearest_rule_threshold NUMERIC(20, 6)"
    )
    op.execute("ALTER TABLE public.fb_ads ADD COLUMN IF NOT EXISTS nearest_rule_stage VARCHAR(16)")


def downgrade() -> None:
    raise RuntimeError("operator rule context migration is forward-only")
