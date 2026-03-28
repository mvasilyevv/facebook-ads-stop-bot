# -*- coding: utf-8 -*-
"""Исправить уникальный индекс ad_snapshots: только по fb_ad_id.

Старый индекс (offer_id, fb_ad_id) допускал дубликаты при offer_id=NULL,
потому что NULL != NULL в PostgreSQL.

Revision ID: 003_fix_snapshot_index
Revises: 002_max_attempts_failed
Create Date: 2026-03-26
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "003_fix_snapshot_index"
down_revision: str | None = "002_max_attempts_failed"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    # Удаляем дубликаты — оставляем только самый свежий снэпшот для каждого fb_ad_id
    op.execute("""
        DELETE FROM ad_snapshots
        WHERE id NOT IN (
            SELECT DISTINCT ON (fb_ad_id) id
            FROM ad_snapshots
            ORDER BY fb_ad_id, last_observed_at DESC
        )
    """)

    # Удаляем старый индекс
    op.drop_index("uq_ad_snapshot_offer_fb_ad", table_name="ad_snapshots")

    # Создаём новый уникальный индекс только по fb_ad_id
    op.create_index(
        "uq_ad_snapshot_fb_ad",
        "ad_snapshots",
        ["fb_ad_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_ad_snapshot_fb_ad", table_name="ad_snapshots")
    op.create_index(
        "uq_ad_snapshot_offer_fb_ad",
        "ad_snapshots",
        ["offer_id", "fb_ad_id"],
        unique=True,
    )
