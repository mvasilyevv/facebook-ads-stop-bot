"""offer_creative_seq + campaign_creative (per-offer нумерация кодов + реестр).

Revision ID: 0028_creative_seq_ledger
Revises: 0027_drop_offer_default_page_id
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0028_creative_seq_ledger"
down_revision = "0027_drop_offer_default_page_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "offer_creative_seq",
        sa.Column("offer_code", sa.String(64), primary_key=True),
        sa.Column("next_seq", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.create_table(
        "campaign_creative",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("offer_code", sa.String(64), nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("meta_creative_id", sa.String(64), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("offer_code", "code", name="uq_campaign_creative_offer_code"),
    )
    op.create_index("ix_campaign_creative_offer_code", "campaign_creative", ["offer_code"])

    # Backfill: чтобы новые коды не наехали на коды старых заливов, ставим next_seq =
    # суммарному числу уже созданных креативов по офферу (из campaign_run.created_meta_ids).
    op.execute(
        """
        INSERT INTO offer_creative_seq (offer_code, next_seq)
        SELECT config->>'offer_code' AS offer_code,
               SUM(COALESCE(jsonb_array_length(created_meta_ids->'creatives'), 0)) AS next_seq
        FROM campaign_run
        WHERE config->>'offer_code' IS NOT NULL
          AND created_meta_ids ? 'creatives'
        GROUP BY config->>'offer_code'
        ON CONFLICT (offer_code) DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_index("ix_campaign_creative_offer_code", table_name="campaign_creative")
    op.drop_table("campaign_creative")
    op.drop_table("offer_creative_seq")
