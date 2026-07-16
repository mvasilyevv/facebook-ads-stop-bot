"""Use a 30-second default observer cadence.

Revision ID: 0036_observer_30s_default
Revises: 0035_adsetpro_rollback_compat

The old 90-second default was safe for the retired DOM scanner but adds avoidable
money exposure now that steady-state scans use cached-session am_tabular requests.
Only rows still carrying the old default are migrated; explicit custom intervals
are preserved.
"""

from __future__ import annotations

from alembic import op

revision = "0036_observer_30s_default"
down_revision = "0035_adsetpro_rollback_compat"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE observer_config ALTER COLUMN interval_seconds SET DEFAULT 30")
    op.execute("UPDATE observer_config SET interval_seconds = 30 WHERE interval_seconds = 90")


def downgrade() -> None:
    # Do not rewrite live operator settings on rollback; restore only the schema default.
    op.execute("ALTER TABLE observer_config ALTER COLUMN interval_seconds SET DEFAULT 90")
