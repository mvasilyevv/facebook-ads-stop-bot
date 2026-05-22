"""scan_runs and stale_data_threshold

Revision ID: 5b3af4f6df36
Revises: 884763540a4c
Create Date: 2026-05-22 14:31:55.184487
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "5b3af4f6df36"
down_revision = "884763540a4c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scan_runs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("scan_id", sa.BigInteger(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("rows_total", sa.Integer(), nullable=True),
        sa.Column("rows_partial", sa.Integer(), nullable=True),
        sa.Column("rows_with_data", sa.Integer(), nullable=True),
        sa.Column("alerts_warning", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("alerts_stop", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("phase_timings", postgresql.JSONB(), nullable=True),
        sa.Column("warnings", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("empty_reason", sa.String(64), nullable=True),
        sa.Column("error_kind", sa.String(64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("threat_level", sa.String(32), nullable=True),
        sa.Column("next_interval_s", sa.Integer(), nullable=True),
    )
    op.create_index("scan_runs_started_at_idx", "scan_runs", ["started_at"])
    op.create_index(
        "scan_runs_outcome_idx",
        "scan_runs",
        ["outcome"],
        postgresql_where=sa.text("outcome != 'OK'"),
    )

    op.add_column(
        "observer_settings",
        sa.Column(
            "stale_data_threshold",
            sa.Numeric(3, 2),
            nullable=False,
            server_default="0.9",
        ),
    )


def downgrade() -> None:
    op.drop_column("observer_settings", "stale_data_threshold")
    op.drop_index("scan_runs_outcome_idx", table_name="scan_runs")
    op.drop_index("scan_runs_started_at_idx", table_name="scan_runs")
    op.drop_table("scan_runs")
