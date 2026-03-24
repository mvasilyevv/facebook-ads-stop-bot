"""Добавляет fast-stop pipeline, watchlist и очередь действий.

Revision ID: 20260323_0006
Revises: 20260323_0005
Create Date: 2026-03-23 22:15:00
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260323_0006"
down_revision = "20260323_0005"
branch_labels = None
depends_on = None


_RISK_BAND_ENUM = postgresql.ENUM(
    "SAFE",
    "WATCH",
    "STOP",
    name="risk_band_enum",
    create_type=False,
)
_SCAN_PIPELINE_KIND_ENUM = postgresql.ENUM(
    "FULL_SCAN",
    "TARGETED_RECHECK",
    name="scan_pipeline_kind_enum",
    create_type=False,
)
_ACTION_JOB_STATUS_ENUM = postgresql.ENUM(
    "QUEUED",
    "RUNNING",
    "RETRYING",
    "SUCCEEDED",
    "FAILED",
    "CANCELLED",
    name="action_job_status_enum",
    create_type=False,
)
_ACTION_TYPE_ENUM = postgresql.ENUM(
    "PAUSE",
    "RESUME",
    name="action_type_enum",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    _RISK_BAND_ENUM.create(bind, checkfirst=True)
    _SCAN_PIPELINE_KIND_ENUM.create(bind, checkfirst=True)
    _ACTION_JOB_STATUS_ENUM.create(bind, checkfirst=True)

    op.add_column(
        "ads",
        sa.Column(
            "risk_band",
            _RISK_BAND_ENUM,
            nullable=False,
            server_default="SAFE",
        ),
    )
    op.add_column("ads", sa.Column("last_risk_reason", sa.String(length=500), nullable=True))
    op.add_column("ads", sa.Column("last_risk_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(op.f("ix_ads_risk_band"), "ads", ["risk_band"], unique=False)
    op.create_index(
        "ix_ads_risk_band_last_risk_at",
        "ads",
        ["risk_band", "last_risk_at"],
        unique=False,
    )

    op.add_column(
        "scan_runs",
        sa.Column(
            "pipeline_kind",
            _SCAN_PIPELINE_KIND_ENUM,
            nullable=False,
            server_default="FULL_SCAN",
        ),
    )
    op.add_column(
        "scan_runs",
        sa.Column(
            "trigger_source",
            sa.String(length=64),
            nullable=False,
            server_default="scheduler",
        ),
    )
    op.add_column(
        "scan_runs",
        sa.Column(
            "target_fb_ad_ids",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
    )
    op.add_column(
        "scan_runs",
        sa.Column("collect_ms", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "scan_runs",
        sa.Column("evaluate_ms", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "scan_runs",
        sa.Column("persist_ms", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "scan_runs",
        sa.Column("queue_ms", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "scan_runs",
        sa.Column("action_jobs_enqueued", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index(
        op.f("ix_scan_runs_pipeline_kind"),
        "scan_runs",
        ["pipeline_kind"],
        unique=False,
    )
    op.create_index(
        "ix_scan_runs_pipeline_kind_started_at",
        "scan_runs",
        ["pipeline_kind", "started_at"],
        unique=False,
    )

    op.create_table(
        "watchlist_entries",
        sa.Column("ad_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("fb_ad_id", sa.String(length=64), nullable=False),
        sa.Column("profile_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("browser_host_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("risk_band", _RISK_BAND_ENUM, nullable=False),
        sa.Column("priority_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_check_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_reason", sa.String(length=500), nullable=True),
        sa.Column("last_metrics_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_scan_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["ad_id"], ["ads.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["browser_host_id"], ["browser_hosts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["source_scan_run_id"], ["scan_runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_watchlist_entries")),
    )
    op.create_index(
        op.f("ix_watchlist_entries_fb_ad_id"),
        "watchlist_entries",
        ["fb_ad_id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_watchlist_entries_next_check_at"),
        "watchlist_entries",
        ["next_check_at"],
        unique=False,
    )
    op.create_index(
        "ix_watchlist_entries_next_check_priority",
        "watchlist_entries",
        ["next_check_at", "priority_score"],
        unique=False,
    )
    op.create_index(
        "ix_watchlist_entries_profile_next_check",
        "watchlist_entries",
        ["profile_id", "next_check_at"],
        unique=False,
    )

    op.create_table(
        "action_jobs",
        sa.Column("decision_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("ad_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("fb_ad_id", sa.String(length=64), nullable=False),
        sa.Column("profile_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("browser_host_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("action_type", _ACTION_TYPE_ENUM, nullable=False),
        sa.Column(
            "status",
            _ACTION_JOB_STATUS_ENUM,
            nullable=False,
            server_default="QUEUED",
        ),
        sa.Column("priority_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["decision_id"], ["decisions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["ad_id"], ["ads.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["browser_host_id"], ["browser_hosts.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_action_jobs")),
    )
    op.create_index(op.f("ix_action_jobs_fb_ad_id"), "action_jobs", ["fb_ad_id"], unique=False)
    op.create_index(op.f("ix_action_jobs_status"), "action_jobs", ["status"], unique=False)
    op.create_index(
        op.f("ix_action_jobs_next_attempt_at"),
        "action_jobs",
        ["next_attempt_at"],
        unique=False,
    )
    op.create_index(
        "ix_action_jobs_next_attempt_priority",
        "action_jobs",
        ["next_attempt_at", "priority_score"],
        unique=False,
    )
    op.create_index(
        "ix_action_jobs_profile_status_created",
        "action_jobs",
        ["profile_id", "status", "created_at"],
        unique=False,
    )
    op.create_index(
        "uq_action_jobs_active_pause_per_ad",
        "action_jobs",
        ["fb_ad_id", "action_type"],
        unique=True,
        sqlite_where=sa.text("status IN ('QUEUED', 'RUNNING', 'RETRYING')"),
        postgresql_where=sa.text("status IN ('QUEUED', 'RUNNING', 'RETRYING')"),
    )

    op.execute(
        sa.text(
            """
            UPDATE system_settings
            SET key = 'full_scan_interval_seconds'
            WHERE key = 'scan_interval_seconds'
            """
        )
    )
    _insert_setting_if_missing(
        key="recheck_interval_seconds",
        value="15",
        description="Интервал быстрого перепросмотра рискованных объявлений в секундах",
    )
    _insert_setting_if_missing(
        key="full_scan_profile_concurrency",
        value="2",
        description="Максимум профилей в одном полном цикле сканирования",
    )
    _insert_setting_if_missing(
        key="action_worker_concurrency",
        value="2",
        description="Максимум параллельных профилей в очереди действий",
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            DELETE FROM system_settings
            WHERE key IN (
                'recheck_interval_seconds',
                'full_scan_profile_concurrency',
                'action_worker_concurrency'
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE system_settings
            SET key = 'scan_interval_seconds'
            WHERE key = 'full_scan_interval_seconds'
            """
        )
    )

    op.drop_index("uq_action_jobs_active_pause_per_ad", table_name="action_jobs")
    op.drop_index("ix_action_jobs_profile_status_created", table_name="action_jobs")
    op.drop_index("ix_action_jobs_next_attempt_priority", table_name="action_jobs")
    op.drop_index(op.f("ix_action_jobs_next_attempt_at"), table_name="action_jobs")
    op.drop_index(op.f("ix_action_jobs_status"), table_name="action_jobs")
    op.drop_index(op.f("ix_action_jobs_fb_ad_id"), table_name="action_jobs")
    op.drop_table("action_jobs")

    op.drop_index("ix_watchlist_entries_profile_next_check", table_name="watchlist_entries")
    op.drop_index("ix_watchlist_entries_next_check_priority", table_name="watchlist_entries")
    op.drop_index(op.f("ix_watchlist_entries_next_check_at"), table_name="watchlist_entries")
    op.drop_index(op.f("ix_watchlist_entries_fb_ad_id"), table_name="watchlist_entries")
    op.drop_table("watchlist_entries")

    op.drop_index("ix_scan_runs_pipeline_kind_started_at", table_name="scan_runs")
    op.drop_index(op.f("ix_scan_runs_pipeline_kind"), table_name="scan_runs")
    op.drop_column("scan_runs", "action_jobs_enqueued")
    op.drop_column("scan_runs", "queue_ms")
    op.drop_column("scan_runs", "persist_ms")
    op.drop_column("scan_runs", "evaluate_ms")
    op.drop_column("scan_runs", "collect_ms")
    op.drop_column("scan_runs", "target_fb_ad_ids")
    op.drop_column("scan_runs", "trigger_source")
    op.drop_column("scan_runs", "pipeline_kind")

    op.drop_index("ix_ads_risk_band_last_risk_at", table_name="ads")
    op.drop_index(op.f("ix_ads_risk_band"), table_name="ads")
    op.drop_column("ads", "last_risk_at")
    op.drop_column("ads", "last_risk_reason")
    op.drop_column("ads", "risk_band")

    bind = op.get_bind()
    _ACTION_JOB_STATUS_ENUM.drop(bind, checkfirst=True)
    _SCAN_PIPELINE_KIND_ENUM.drop(bind, checkfirst=True)
    _RISK_BAND_ENUM.drop(bind, checkfirst=True)


def _insert_setting_if_missing(*, key: str, value: str, description: str) -> None:
    bind = op.get_bind()
    exists = bind.execute(
        sa.text("SELECT 1 FROM system_settings WHERE key = :key"),
        {"key": key},
    ).scalar()
    if exists:
        return
    bind.execute(
        sa.text(
            """
            INSERT INTO system_settings (id, key, value, description)
            VALUES (:id, :key, :value, :description)
            """
        ),
        {
            "id": str(uuid.uuid4()),
            "key": key,
            "value": value,
            "description": description,
        },
    )
