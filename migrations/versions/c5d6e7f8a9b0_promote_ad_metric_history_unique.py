"""Преобразовать unique-индекс ad_metric_history в UNIQUE CONSTRAINT.

ON CONFLICT ON CONSTRAINT требует именно constraint, а не unique-индекс.
USING INDEX переиспользует существующий индекс без блокировки и пересборки.

Revision ID: c5d6e7f8a9b0
Revises: d0e1f2a3b4c5
Create Date: 2026-05-07
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "c5d6e7f8a9b0"
down_revision = "d0e1f2a3b4c5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Идемпотентно: если constraint уже существует, ничего не делаем.
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'uq_ad_metric_history_ad_ts'
            ) THEN
                ALTER TABLE ad_metric_history
                    ADD CONSTRAINT uq_ad_metric_history_ad_ts
                    UNIQUE USING INDEX uq_ad_metric_history_ad_ts;
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    # Откат: снимаем constraint и пересоздаём как unique-индекс с тем же именем.
    op.execute(
        "ALTER TABLE ad_metric_history DROP CONSTRAINT IF EXISTS uq_ad_metric_history_ad_ts;"
    )
    op.create_index(
        "uq_ad_metric_history_ad_ts",
        "ad_metric_history",
        ["ad_id", "cycle_ts"],
        unique=True,
    )
