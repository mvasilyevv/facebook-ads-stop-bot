# -*- coding: utf-8 -*-
"""Make campaign presets explicit snapshots of repeatable wizard fields.

Legacy identity/offer columns remain nullable so an upgrade does not destroy
saved data. New API code ignores them. Existing presets receive honest empty or
NULL values for fields the old contract never stored and must be edited before
they become complete reusable templates.
"""

from __future__ import annotations

from alembic import op

revision = "0004_campaign_preset_snapshot"
down_revision = "0003_tighten_retention_policy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE public.campaign_preset
            ALTER COLUMN act_id DROP NOT NULL,
            ALTER COLUMN page_id DROP NOT NULL,
            ALTER COLUMN pixel_id DROP NOT NULL,
            ADD COLUMN countries jsonb NOT NULL DEFAULT '[]'::jsonb,
            ADD COLUMN age_min integer NOT NULL DEFAULT 21,
            ADD COLUMN age_max integer NOT NULL DEFAULT 65,
            ADD COLUMN genders jsonb NOT NULL DEFAULT '[]'::jsonb,
            ADD COLUMN placements jsonb NOT NULL DEFAULT '[]'::jsonb,
            ADD COLUMN budget_level varchar(16) NOT NULL DEFAULT 'campaign',
            ADD COLUMN daily_budget varchar(32)
        """
    )
    # Purchase is a product rule, including for rows created before this revision.
    op.execute(
        """
        UPDATE public.campaign_preset
        SET custom_event_type = 'PURCHASE', updated_at = now()
        WHERE custom_event_type IS DISTINCT FROM 'PURCHASE'
        """
    )
    op.execute(
        """
        ALTER TABLE public.campaign_preset
            ADD CONSTRAINT ck_campaign_preset_purchase_only
                CHECK (custom_event_type = 'PURCHASE'),
            ADD CONSTRAINT ck_campaign_preset_age_range
                CHECK (age_min BETWEEN 18 AND 65 AND age_max BETWEEN 18 AND 65
                       AND age_min <= age_max),
            ADD CONSTRAINT ck_campaign_preset_countries_array
                CHECK (jsonb_typeof(countries) = 'array'),
            ADD CONSTRAINT ck_campaign_preset_genders_array
                CHECK (jsonb_typeof(genders) = 'array'),
            ADD CONSTRAINT ck_campaign_preset_placements_array
                CHECK (jsonb_typeof(placements) = 'array'),
            ADD CONSTRAINT ck_campaign_preset_budget_level
                CHECK (budget_level IN ('campaign', 'adset')),
            ADD CONSTRAINT ck_campaign_preset_daily_budget
                CHECK (
                    daily_budget IS NULL
                    OR (
                        daily_budget ~ '^(0|[1-9][0-9]*)(\\.[0-9]+)?$'
                        AND daily_budget ~ '[1-9]'
                    )
                )
        """
    )


def downgrade() -> None:
    raise RuntimeError("campaign preset snapshot migration is forward-only")
