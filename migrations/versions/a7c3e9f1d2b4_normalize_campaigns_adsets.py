"""normalize_campaigns_adsets

Revision ID: a7c3e9f1d2b4
Revises: b2912a123fdf
Create Date: 2026-04-04

Рефакторинг: нормализация FB-кампаний и адсетов в отдельные таблицы.
fb_ads теперь ссылается на fb_adsets (а не хранит campaign_name/adset_name).
ad_snapshots и offers очищены от дублирующих колонок.
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7c3e9f1d2b4"
down_revision: str | None = "b2912a123fdf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- 1. Создаём таблицу fb_campaigns ---
    op.create_table(
        "fb_campaigns",
        sa.Column("id", sa.Uuid(), primary_key=True, default=sa.text("gen_random_uuid()")),
        sa.Column("campaign_name", sa.String(255), nullable=False),
        sa.Column(
            "offer_id",
            sa.Uuid(),
            sa.ForeignKey("offers.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("offer_code", sa.String(100), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=True,
        ),
    )
    op.create_index("uq_fb_campaign_name", "fb_campaigns", ["campaign_name"], unique=True)
    op.create_index("ix_fb_campaign_offer_id", "fb_campaigns", ["offer_id"])

    # --- 2. Создаём таблицу fb_adsets ---
    op.create_table(
        "fb_adsets",
        sa.Column("id", sa.Uuid(), primary_key=True, default=sa.text("gen_random_uuid()")),
        sa.Column("adset_name", sa.String(255), nullable=False),
        sa.Column(
            "campaign_id",
            sa.Uuid(),
            sa.ForeignKey("fb_campaigns.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=True,
        ),
    )
    op.create_index(
        "uq_fb_adset_campaign_name", "fb_adsets", ["campaign_id", "adset_name"], unique=True
    )
    op.create_index("ix_fb_adset_campaign_id", "fb_adsets", ["campaign_id"])

    # --- 3. Миграция данных: заполняем fb_campaigns из fb_ads ---
    op.execute(
        """
        INSERT INTO fb_campaigns (id, campaign_name, offer_id, offer_code,
                                  first_seen_at, last_seen_at, created_at, updated_at)
        SELECT gen_random_uuid(), campaign_name, offer_id, offer_code,
               MIN(first_seen_at), MAX(last_seen_at), NOW(), NOW()
        FROM fb_ads
        WHERE campaign_name != ''
        GROUP BY campaign_name, offer_id, offer_code
        """
    )
    # Заглушка для объявлений с пустым campaign_name
    op.execute(
        """
        INSERT INTO fb_campaigns (id, campaign_name, offer_id, offer_code,
                                  first_seen_at, last_seen_at, created_at, updated_at)
        SELECT gen_random_uuid(), '', NULL, NULL,
               MIN(first_seen_at), MAX(last_seen_at), NOW(), NOW()
        FROM fb_ads
        WHERE campaign_name = ''
        HAVING COUNT(*) > 0
        """
    )

    # --- 4. Миграция данных: заполняем fb_adsets из fb_ads ---
    op.execute(
        """
        INSERT INTO fb_adsets (id, adset_name, campaign_id,
                               first_seen_at, last_seen_at, created_at, updated_at)
        SELECT gen_random_uuid(), fa.adset_name, fc.id,
               MIN(fa.first_seen_at), MAX(fa.last_seen_at), NOW(), NOW()
        FROM fb_ads fa
        JOIN fb_campaigns fc ON fc.campaign_name = fa.campaign_name
        WHERE fa.adset_name != ''
        GROUP BY fa.adset_name, fc.id
        """
    )
    # Заглушки для объявлений с пустым adset_name — по одной на каждую кампанию
    op.execute(
        """
        INSERT INTO fb_adsets (id, adset_name, campaign_id,
                               first_seen_at, last_seen_at, created_at, updated_at)
        SELECT gen_random_uuid(), '', fc.id,
               MIN(fa.first_seen_at), MAX(fa.last_seen_at), NOW(), NOW()
        FROM fb_ads fa
        JOIN fb_campaigns fc ON fc.campaign_name = fa.campaign_name
        WHERE fa.adset_name = ''
        GROUP BY fc.id
        HAVING COUNT(*) > 0
        """
    )

    # --- 5. Добавляем adset_id в fb_ads (nullable) ---
    op.add_column("fb_ads", sa.Column("adset_id", sa.Uuid(), nullable=True))

    # --- 6. Заполняем adset_id ---
    op.execute(
        """
        UPDATE fb_ads
        SET adset_id = fs.id
        FROM fb_adsets fs
        JOIN fb_campaigns fc ON fs.campaign_id = fc.id
        WHERE fc.campaign_name = fb_ads.campaign_name
          AND fs.adset_name = fb_ads.adset_name
        """
    )

    # --- 7. Делаем adset_id NOT NULL и добавляем FK ---
    op.alter_column("fb_ads", "adset_id", nullable=False)
    op.create_foreign_key(
        "fk_fb_ads_adset_id", "fb_ads", "fb_adsets", ["adset_id"], ["id"], ondelete="CASCADE"
    )

    # --- 8. Удаляем старые индексы fb_ads (до дропа колонок, IF EXISTS) ---
    op.execute("DROP INDEX IF EXISTS ix_fb_ad_offer_id")
    op.execute("DROP INDEX IF EXISTS ix_fb_ad_campaign_name")

    # --- 9. Удаляем устаревшие колонки из fb_ads ---
    op.drop_column("fb_ads", "campaign_name")
    op.drop_column("fb_ads", "adset_name")
    op.drop_column("fb_ads", "offer_id")
    op.drop_column("fb_ads", "offer_code")

    # --- 10. Создаём новый индекс на adset_id ---
    op.create_index("ix_fb_ad_adset_id", "fb_ads", ["adset_id"])

    # --- 11. Удаляем старый индекс ad_snapshots (до дропа колонок, IF EXISTS) ---
    op.execute("DROP INDEX IF EXISTS ix_ad_snapshot_offer_alert")

    # --- 12. Удаляем устаревшие колонки из ad_snapshots ---
    op.drop_column("ad_snapshots", "campaign_name")
    op.drop_column("ad_snapshots", "adset_name")
    op.drop_column("ad_snapshots", "resolved_offer_code")
    op.drop_column("ad_snapshots", "offer_id")
    op.drop_column("ad_snapshots", "ad_name")

    # --- 13. Создаём новый индекс на ad_id в ad_snapshots ---
    op.create_index("ix_ad_snapshot_ad_id", "ad_snapshots", ["ad_id"])

    # --- 14. Удаляем колонку name из offers ---
    op.drop_column("offers", "name")


def downgrade() -> None:
    # --- Восстанавливаем name в offers ---
    op.add_column("offers", sa.Column("name", sa.String(255), server_default="", nullable=False))
    op.execute("UPDATE offers SET name = code")

    # --- Восстанавливаем колонки ad_snapshots ---
    op.drop_index("ix_ad_snapshot_ad_id", table_name="ad_snapshots")
    op.add_column(
        "ad_snapshots", sa.Column("ad_name", sa.String(255), server_default="", nullable=False)
    )
    op.add_column("ad_snapshots", sa.Column("offer_id", sa.Uuid(), nullable=True))
    op.add_column(
        "ad_snapshots",
        sa.Column("resolved_offer_code", sa.String(100), server_default="", nullable=True),
    )
    op.add_column(
        "ad_snapshots", sa.Column("adset_name", sa.String(255), server_default="", nullable=False)
    )
    op.add_column(
        "ad_snapshots",
        sa.Column("campaign_name", sa.String(255), server_default="", nullable=False),
    )
    op.create_index("ix_ad_snapshot_offer_alert", "ad_snapshots", ["offer_id", "alert_state"])

    # --- Восстанавливаем колонки fb_ads ---
    op.drop_index("ix_fb_ad_adset_id", table_name="fb_ads")
    op.create_index("ix_fb_ad_campaign_name", "fb_ads", ["campaign_name"])
    op.create_index("ix_fb_ad_offer_id", "fb_ads", ["offer_id"])
    op.add_column(
        "fb_ads", sa.Column("offer_code", sa.String(100), server_default="", nullable=True)
    )
    op.add_column("fb_ads", sa.Column("offer_id", sa.Uuid(), nullable=True))
    op.add_column(
        "fb_ads", sa.Column("adset_name", sa.String(255), server_default="", nullable=False)
    )
    op.add_column(
        "fb_ads", sa.Column("campaign_name", sa.String(255), server_default="", nullable=False)
    )

    # Миграция данных обратно: восстанавливаем campaign_name, adset_name, offer_id, offer_code
    op.execute(
        """
        UPDATE fb_ads
        SET campaign_name = fc.campaign_name,
            adset_name = fs.adset_name,
            offer_id = fc.offer_id,
            offer_code = fc.offer_code
        FROM fb_adsets fs
        JOIN fb_campaigns fc ON fs.campaign_id = fc.id
        WHERE fb_ads.adset_id = fs.id
        """
    )

    # Восстанавливаем данные ad_snapshots
    op.execute(
        """
        UPDATE ad_snapshots
        SET campaign_name = fc.campaign_name,
            adset_name = fs.adset_name,
            ad_name = fa.ad_name,
            offer_id = fc.offer_id,
            resolved_offer_code = fc.offer_code
        FROM fb_ads fa
        JOIN fb_adsets fs ON fa.adset_id = fs.id
        JOIN fb_campaigns fc ON fs.campaign_id = fc.id
        WHERE ad_snapshots.ad_id = fa.id
        """
    )

    # Удаляем adset_id FK и колонку
    op.drop_constraint("fk_fb_ads_adset_id", "fb_ads", type_="foreignkey")
    op.drop_column("fb_ads", "adset_id")

    # Удаляем таблицы нормализации
    op.drop_index("ix_fb_adset_campaign_id", table_name="fb_adsets")
    op.drop_index("uq_fb_adset_campaign_name", table_name="fb_adsets")
    op.drop_table("fb_adsets")

    op.drop_index("ix_fb_campaign_offer_id", table_name="fb_campaigns")
    op.drop_index("uq_fb_campaign_name", table_name="fb_campaigns")
    op.drop_table("fb_campaigns")
