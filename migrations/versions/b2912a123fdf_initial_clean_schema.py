"""Базовая схема для пустой базы данных.

Revision ID: b2912a123fdf
Revises:
Create Date: 2026-04-03 22:26:01.832184
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b2912a123fdf"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


alert_stage_enum = postgresql.ENUM(
    "WARNING",
    "STOP",
    "EARLY_SIGNAL",
    name="alert_stage_enum",
    create_type=False,
)
alert_state_enum = postgresql.ENUM(
    "NORMAL",
    "WARNING_SENT",
    "STOP_SENT",
    "CLAIMED",
    "DISABLED",
    "EARLY_SIGNAL_SENT",
    name="alert_state_enum",
    create_type=False,
)
disable_task_status_enum = postgresql.ENUM(
    "PENDING",
    "RUNNING",
    "RETRYING",
    "SUCCEEDED",
    "CANCELLED",
    "FAILED",
    name="disable_task_status_enum",
    create_type=False,
)
enable_recommendation_level_enum = postgresql.ENUM(
    "OK",
    "WARNING",
    "EARLY_SIGNAL",
    name="enable_recommendation_level_enum",
    create_type=False,
)
enable_task_status_enum = postgresql.ENUM(
    "PENDING",
    "RUNNING",
    "RETRYING",
    "SUCCEEDED",
    "CANCELLED",
    "FAILED",
    name="enable_task_status_enum",
    create_type=False,
)
telegram_delivery_mode_enum = postgresql.ENUM(
    "PRIVATE_CHAT",
    "FORUM_GROUP",
    name="telegram_delivery_mode_enum",
    create_type=False,
)
telegram_notification_stream_enum = postgresql.ENUM(
    "WARNING",
    "STOP",
    "ENABLE",
    "EARLY",
    name="telegram_notification_stream_enum",
    create_type=False,
)


def _id_column() -> sa.Column[str]:
    return sa.Column("id", sa.Uuid(), nullable=False)


def _timestamps() -> tuple[sa.Column[sa.DateTime], sa.Column[sa.DateTime]]:
    return (
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def _create_enums() -> None:
    bind = op.get_bind()
    for enum_type in (
        alert_stage_enum,
        alert_state_enum,
        disable_task_status_enum,
        enable_recommendation_level_enum,
        enable_task_status_enum,
        telegram_delivery_mode_enum,
        telegram_notification_stream_enum,
    ):
        enum_type.create(bind, checkfirst=True)


def _drop_enums() -> None:
    bind = op.get_bind()
    for enum_type in (
        telegram_notification_stream_enum,
        telegram_delivery_mode_enum,
        enable_task_status_enum,
        enable_recommendation_level_enum,
        disable_task_status_enum,
        alert_state_enum,
        alert_stage_enum,
    ):
        enum_type.drop(bind, checkfirst=True)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    _create_enums()

    op.create_table(
        "observer_settings",
        _id_column(),
        *_timestamps(),
        sa.Column("singleton_key", sa.String(length=32), nullable=False),
        sa.Column("interval_seconds", sa.Integer(), nullable=False),
        sa.Column("jitter_seconds", sa.Integer(), nullable=False),
        sa.Column("warning_percent_of_stop", sa.Numeric(6, 2), nullable=False),
        sa.Column("stop_percent_of_base", sa.Numeric(6, 2), nullable=False),
        sa.Column("cpc_warning_percent_of_stop", sa.Numeric(6, 2), nullable=False),
        sa.Column("cpc_stop_percent_of_base", sa.Numeric(6, 2), nullable=False),
        sa.Column("cpl_warning_percent_of_stop", sa.Numeric(6, 2), nullable=False),
        sa.Column("cpl_stop_percent_of_base", sa.Numeric(6, 2), nullable=False),
        sa.Column("cpr_warning_percent_of_stop", sa.Numeric(6, 2), nullable=False),
        sa.Column("cpr_stop_percent_of_base", sa.Numeric(6, 2), nullable=False),
        sa.Column("is_scanning_enabled", sa.Boolean(), nullable=False),
        sa.Column("scan_requested", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("cabinet_day_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("worker_status", sa.String(length=32), nullable=True),
        sa.Column("worker_message", sa.String(length=500), nullable=True),
        sa.Column("worker_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("worker_last_error", sa.String(length=500), nullable=True),
        sa.Column("worker_last_error_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "install_cost",
            sa.Numeric(8, 4),
            server_default=sa.text("0.02"),
            nullable=False,
        ),
        sa.Column(
            "agent_commission_percent",
            sa.Numeric(6, 2),
            server_default=sa.text("3"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("singleton_key"),
    )
    op.create_index(
        "ix_observer_settings_cabinet_day_started_at",
        "observer_settings",
        ["cabinet_day_started_at"],
    )

    op.create_table(
        "cabinet_day_archives",
        _id_column(),
        *_timestamps(),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reset_detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ads_count", sa.Integer(), nullable=False),
        sa.Column("summary_json", sa.JSON(), nullable=False),
        sa.Column("campaigns_json", sa.JSON(), nullable=False),
        sa.Column("offer_stats_json", sa.JSON(), nullable=False),
        sa.Column("ads_json", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_cabinet_day_archives_started_at", "cabinet_day_archives", ["started_at"])
    op.create_index("ix_cabinet_day_archives_ended_at", "cabinet_day_archives", ["ended_at"])
    op.create_index(
        "ix_cabinet_day_archives_reset_detected_at",
        "cabinet_day_archives",
        ["reset_detected_at"],
    )

    op.create_table(
        "telegram_settings",
        _id_column(),
        *_timestamps(),
        sa.Column("singleton_key", sa.String(length=32), nullable=False),
        sa.Column("bot_token_encrypted", sa.Text(), nullable=False),
        sa.Column("chat_id", sa.String(length=64), nullable=False),
        sa.Column("owner_telegram_user_id", sa.String(length=64), nullable=False),
        sa.Column("owner_username", sa.String(length=128), nullable=False),
        sa.Column("owner_first_name", sa.String(length=128), nullable=False),
        sa.Column("is_authorized", sa.Boolean(), nullable=False),
        sa.Column("delivery_mode", telegram_delivery_mode_enum, nullable=False),
        sa.Column("auth_code", sa.String(length=16), nullable=False),
        sa.Column("bot_username", sa.String(length=128), nullable=False),
        sa.Column("poller_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("control_topic_id", sa.Integer(), nullable=True),
        sa.Column("warning_topic_id", sa.Integer(), nullable=True),
        sa.Column("stop_topic_id", sa.Integer(), nullable=True),
        sa.Column("early_topic_id", sa.Integer(), nullable=True),
        sa.Column("enable_topic_id", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("singleton_key"),
    )

    op.create_table(
        "offers",
        _id_column(),
        *_timestamps(),
        sa.Column("name", sa.String(length=255), server_default="", nullable=False),
        sa.Column("code", sa.String(length=100), nullable=False),
        sa.Column("cpa_amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("payout_per_deposit", sa.Float(), server_default="0", nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_offers_code", "offers", ["code"], unique=True)

    op.create_table(
        "offer_rule_configs",
        _id_column(),
        *_timestamps(),
        sa.Column("offer_id", sa.Uuid(), nullable=False),
        sa.Column("cpc_percent_enabled", sa.Boolean(), nullable=False),
        sa.Column("cpc_percent_stop", sa.Numeric(8, 2), nullable=False),
        sa.Column("cpl_percent_enabled", sa.Boolean(), nullable=False),
        sa.Column("cpl_percent_stop", sa.Numeric(8, 2), nullable=False),
        sa.Column("cpr_percent_enabled", sa.Boolean(), nullable=False),
        sa.Column("cpr_percent_stop", sa.Numeric(8, 2), nullable=False),
        sa.Column("regs_no_dep_enabled", sa.Boolean(), nullable=False),
        sa.Column("regs_no_dep_stop_count", sa.Integer(), nullable=False),
        sa.Column("spend_no_dep_enabled", sa.Boolean(), nullable=False),
        sa.Column("spend_no_dep_from_percent", sa.Numeric(8, 2), nullable=False),
        sa.Column("spend_no_dep_to_percent", sa.Numeric(8, 2), nullable=False),
        sa.Column("spend_with_dep_enabled", sa.Boolean(), nullable=False),
        sa.Column("spend_with_dep_from_percent", sa.Numeric(8, 2), nullable=False),
        sa.Column("spend_with_dep_to_percent", sa.Numeric(8, 2), nullable=False),
        sa.Column("frequency_elevated_threshold", sa.Numeric(8, 2), nullable=False),
        sa.Column("frequency_critical_threshold", sa.Numeric(8, 2), nullable=False),
        sa.Column(
            "early_outbound_ctr_signal_min_percent",
            sa.Numeric(8, 4),
            server_default=sa.text("0.80"),
            nullable=False,
        ),
        sa.Column(
            "early_lpv_ratio_signal_enabled",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "early_lpv_ratio_signal_min_percent",
            sa.Numeric(8, 2),
            server_default=sa.text("60"),
            nullable=False,
        ),
        sa.Column(
            "early_outbound_ctr_signal_min_spend_percent",
            sa.Numeric(8, 2),
            server_default=sa.text("5"),
            nullable=False,
        ),
        sa.Column(
            "early_cost_per_lpv_signal_min_views",
            sa.Integer(),
            server_default=sa.text("2"),
            nullable=False,
        ),
        sa.Column(
            "early_lpv_ratio_signal_min_outbound_clicks",
            sa.Integer(),
            server_default=sa.text("5"),
            nullable=False,
        ),
        sa.Column(
            "early_cost_per_lpv_signal_percent_of_cpa",
            sa.Numeric(8, 2),
            server_default=sa.text("5"),
            nullable=False,
        ),
        sa.Column(
            "early_outbound_ctr_signal_enabled",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "early_cost_per_lpv_signal_enabled",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["offer_id"], ["offers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_offer_rule_configs_offer_id", "offer_rule_configs", ["offer_id"], unique=True
    )

    op.create_table(
        "fb_ads",
        _id_column(),
        *_timestamps(),
        sa.Column("fb_ad_id", sa.String(length=32), nullable=False),
        sa.Column("ad_name", sa.String(length=255), nullable=False),
        sa.Column("campaign_name", sa.String(length=255), nullable=False),
        sa.Column("adset_name", sa.String(length=255), nullable=False),
        sa.Column("offer_id", sa.Uuid(), nullable=True),
        sa.Column("offer_code", sa.String(length=100), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["offer_id"], ["offers.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("uq_fb_ad_fb_ad_id", "fb_ads", ["fb_ad_id"], unique=True)
    op.create_index("ix_fb_ad_campaign_name", "fb_ads", ["campaign_name"])
    op.create_index("ix_fb_ad_offer_id", "fb_ads", ["offer_id"])
    op.create_index("ix_fb_ad_last_seen_at", "fb_ads", ["last_seen_at"])

    op.create_table(
        "ad_metric_history",
        _id_column(),
        sa.Column("ad_id", sa.Uuid(), nullable=False),
        sa.Column("cycle_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("spend", sa.Numeric(12, 2), nullable=False),
        sa.Column("reach", sa.Integer(), nullable=False),
        sa.Column("impressions", sa.Integer(), nullable=False),
        sa.Column("clicks", sa.Integer(), nullable=False),
        sa.Column("cpc", sa.Numeric(12, 4), nullable=True),
        sa.Column("ctr", sa.Numeric(12, 4), nullable=True),
        sa.Column("cost_per_result", sa.Numeric(12, 4), nullable=True),
        sa.Column("cpm", sa.Numeric(12, 4), nullable=True),
        sa.Column("frequency", sa.Numeric(12, 4), nullable=True),
        sa.Column("leads", sa.Integer(), nullable=False),
        sa.Column("cost_per_lead", sa.Numeric(12, 4), nullable=True),
        sa.Column("registrations", sa.Integer(), nullable=False),
        sa.Column("cost_per_registration", sa.Numeric(12, 4), nullable=True),
        sa.Column("deposits", sa.Integer(), nullable=False),
        sa.Column("outbound_clicks", sa.Integer(), nullable=False),
        sa.Column("outbound_ctr", sa.Numeric(12, 4), nullable=True),
        sa.Column("landing_page_views", sa.Integer(), nullable=False),
        sa.Column("cost_per_landing_page_view", sa.Numeric(12, 4), nullable=True),
        sa.ForeignKeyConstraint(["ad_id"], ["fb_ads.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ad_metric_history_ad_id", "ad_metric_history", ["ad_id"])
    op.create_index("ix_ad_metric_history_cycle_ts", "ad_metric_history", ["cycle_ts"])
    op.create_index(
        "uq_ad_metric_history_ad_ts",
        "ad_metric_history",
        ["ad_id", "cycle_ts"],
        unique=True,
    )

    op.create_table(
        "ad_snapshots",
        _id_column(),
        *_timestamps(),
        sa.Column("ad_id", sa.Uuid(), nullable=False),
        sa.Column("fb_ad_id", sa.String(length=32), nullable=False),
        sa.Column("ad_name", sa.String(length=255), nullable=False),
        sa.Column("campaign_name", sa.String(length=255), nullable=False),
        sa.Column("adset_name", sa.String(length=255), nullable=False),
        sa.Column("offer_id", sa.Uuid(), nullable=True),
        sa.Column("resolved_offer_code", sa.String(length=100), nullable=True),
        sa.Column("delivery_status", sa.String(length=64), nullable=False),
        sa.Column("spend", sa.Numeric(12, 2), nullable=False),
        sa.Column("budget", sa.String(length=255), nullable=False),
        sa.Column("reach", sa.Integer(), nullable=False),
        sa.Column("impressions", sa.Integer(), nullable=False),
        sa.Column("clicks", sa.Integer(), nullable=False),
        sa.Column("cpc", sa.Numeric(12, 4), nullable=True),
        sa.Column("ctr", sa.Numeric(12, 4), nullable=True),
        sa.Column("cost_per_result", sa.Numeric(12, 4), nullable=True),
        sa.Column("cpm", sa.Numeric(12, 4), nullable=True),
        sa.Column("frequency", sa.Numeric(12, 4), nullable=True),
        sa.Column("leads", sa.Integer(), nullable=False),
        sa.Column("cost_per_lead", sa.Numeric(12, 4), nullable=True),
        sa.Column("registrations", sa.Integer(), nullable=False),
        sa.Column("cost_per_registration", sa.Numeric(12, 4), nullable=True),
        sa.Column("deposits", sa.Integer(), nullable=False),
        sa.Column("outbound_clicks", sa.Integer(), nullable=False),
        sa.Column("outbound_ctr", sa.Numeric(12, 4), nullable=True),
        sa.Column("landing_page_views", sa.Integer(), nullable=False),
        sa.Column("cost_per_landing_page_view", sa.Numeric(12, 4), nullable=True),
        sa.Column("early_signal_rule_codes", sa.JSON(), nullable=False),
        sa.Column("warning_rule_codes", sa.JSON(), nullable=False),
        sa.Column("stop_rule_codes", sa.JSON(), nullable=False),
        sa.Column("current_stage", alert_stage_enum, nullable=True),
        sa.Column("alert_state", alert_state_enum, nullable=False),
        sa.Column("open_state_token", sa.String(length=64), nullable=True),
        sa.Column("telegram_chat_id", sa.String(length=64), nullable=True),
        sa.Column("telegram_message_id", sa.Integer(), nullable=True),
        sa.Column("telegram_group_key", sa.String(length=64), nullable=True),
        sa.Column("last_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("snoozed_until", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["ad_id"], ["fb_ads.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["offer_id"], ["offers.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("uq_ad_snapshot_fb_ad", "ad_snapshots", ["fb_ad_id"], unique=True)
    op.create_index("ix_ad_snapshots_ad_id", "ad_snapshots", ["ad_id"])
    op.create_index("ix_ad_snapshot_alert_state", "ad_snapshots", ["alert_state"])
    op.create_index(
        "ix_ad_snapshot_last_observed",
        "ad_snapshots",
        ["last_observed_at", "alert_state"],
    )
    op.create_index("ix_ad_snapshot_offer_alert", "ad_snapshots", ["offer_id", "alert_state"])
    op.create_index("ix_ad_snapshots_open_state_token", "ad_snapshots", ["open_state_token"])
    op.create_index("ix_ad_snapshots_telegram_group_key", "ad_snapshots", ["telegram_group_key"])

    op.create_table(
        "alert_events",
        _id_column(),
        *_timestamps(),
        sa.Column("ad_id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=True),
        sa.Column("offer_id", sa.Uuid(), nullable=True),
        sa.Column("stage", alert_stage_enum, nullable=False),
        sa.Column("state", alert_state_enum, nullable=False),
        sa.Column("matched_rule_codes", sa.JSON(), nullable=False),
        sa.Column("reason_title", sa.String(length=255), nullable=True),
        sa.Column("reason_text", sa.Text(), nullable=True),
        sa.Column("metrics_json", sa.JSON(), nullable=False),
        sa.Column("message_text", sa.Text(), nullable=True),
        sa.Column("telegram_chat_id", sa.String(length=64), nullable=True),
        sa.Column("telegram_message_id", sa.Integer(), nullable=True),
        sa.Column("telegram_group_key", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(
            ["ad_id"],
            ["fb_ads.id"],
            name="fk_alert_events_ad_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["snapshot_id"], ["ad_snapshots.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["offer_id"], ["offers.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_alert_events_ad_id", "alert_events", ["ad_id"])
    op.create_index("ix_alert_events_snapshot_id", "alert_events", ["snapshot_id"])
    op.create_index("ix_alert_events_offer_id", "alert_events", ["offer_id"])
    op.create_index("ix_alert_events_stage", "alert_events", ["stage"])
    op.create_index("ix_alert_events_state", "alert_events", ["state"])
    op.create_index("ix_alert_event_created_at", "alert_events", ["created_at"])
    op.create_index("ix_alert_events_telegram_message_id", "alert_events", ["telegram_message_id"])
    op.create_index("ix_alert_events_telegram_group_key", "alert_events", ["telegram_group_key"])

    op.create_table(
        "disable_tasks",
        _id_column(),
        *_timestamps(),
        sa.Column("ad_id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=True),
        sa.Column("offer_id", sa.Uuid(), nullable=True),
        sa.Column("open_state_token", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("status", disable_task_status_enum, nullable=False),
        sa.Column("requested_by_telegram_user_id", sa.String(length=64), nullable=True),
        sa.Column("requested_by_username", sa.String(length=255), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["ad_id"],
            ["fb_ads.id"],
            name="fk_disable_tasks_ad_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["snapshot_id"], ["ad_snapshots.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["offer_id"], ["offers.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_disable_tasks_ad_id", "disable_tasks", ["ad_id"])
    op.create_index("ix_disable_tasks_snapshot_id", "disable_tasks", ["snapshot_id"])
    op.create_index("ix_disable_tasks_offer_id", "disable_tasks", ["offer_id"])
    op.create_index("ix_disable_tasks_open_state_token", "disable_tasks", ["open_state_token"])
    op.create_index("ix_disable_tasks_status", "disable_tasks", ["status"])
    op.create_index("ix_disable_tasks_next_retry_at", "disable_tasks", ["next_retry_at"])
    op.create_index(
        "uq_disable_task_idempotency", "disable_tasks", ["idempotency_key"], unique=True
    )
    op.create_index("ix_disable_task_queue", "disable_tasks", ["status", "next_retry_at"])
    op.create_index("ix_disable_task_ad_incident", "disable_tasks", ["ad_id", "open_state_token"])
    op.create_index("ix_disable_task_completed_at", "disable_tasks", ["completed_at"])

    op.create_table(
        "enable_recommendation_events",
        _id_column(),
        *_timestamps(),
        sa.Column("ad_id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=True),
        sa.Column("offer_id", sa.Uuid(), nullable=True),
        sa.Column("delivery_status", sa.String(length=64), nullable=False),
        sa.Column("recommendation_level", enable_recommendation_level_enum, nullable=False),
        sa.Column("matched_rule_codes", sa.JSON(), nullable=False),
        sa.Column("reason_title", sa.String(length=255), nullable=True),
        sa.Column("reason_text", sa.Text(), nullable=True),
        sa.Column("metrics_json", sa.JSON(), nullable=False),
        sa.Column("live_batch_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("telegram_chat_id", sa.String(length=64), nullable=True),
        sa.Column("telegram_message_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["ad_id"],
            ["fb_ads.id"],
            name="fk_enable_recommendation_events_ad_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["snapshot_id"], ["ad_snapshots.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["offer_id"], ["offers.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_enable_recommendation_events_ad_id",
        "enable_recommendation_events",
        ["ad_id"],
    )
    op.create_index(
        "ix_enable_recommendation_events_snapshot_id",
        "enable_recommendation_events",
        ["snapshot_id"],
    )
    op.create_index(
        "ix_enable_recommendation_events_offer_id",
        "enable_recommendation_events",
        ["offer_id"],
    )
    op.create_index(
        "ix_enable_recommendation_events_recommendation_level",
        "enable_recommendation_events",
        ["recommendation_level"],
    )
    op.create_index(
        "ix_enable_recommendation_events_live_batch_started_at",
        "enable_recommendation_events",
        ["live_batch_started_at"],
    )
    op.create_index(
        "uq_enable_recommendation_event_idempotency",
        "enable_recommendation_events",
        ["idempotency_key"],
        unique=True,
    )
    op.create_index(
        "ix_enable_recommendation_events_telegram_message_id",
        "enable_recommendation_events",
        ["telegram_message_id"],
    )

    op.create_table(
        "ad_deposit_corrections",
        _id_column(),
        *_timestamps(),
        sa.Column("ad_id", sa.Uuid(), nullable=False),
        sa.Column("fake_count", sa.Integer(), nullable=False),
        sa.Column("note", sa.String(length=500), nullable=False),
        sa.ForeignKeyConstraint(
            ["ad_id"],
            ["fb_ads.id"],
            name="fk_ad_deposit_corrections_ad_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ad_deposit_corrections_ad_id", "ad_deposit_corrections", ["ad_id"])
    op.create_index(
        "uq_ad_deposit_correction_ad_id",
        "ad_deposit_corrections",
        ["ad_id"],
        unique=True,
    )

    op.create_table(
        "enable_tasks",
        _id_column(),
        *_timestamps(),
        sa.Column("ad_id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=True),
        sa.Column("recommendation_event_id", sa.Uuid(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("status", enable_task_status_enum, nullable=False),
        sa.Column("requested_by_telegram_user_id", sa.String(length=64), nullable=True),
        sa.Column("requested_by_username", sa.String(length=255), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["ad_id"],
            ["fb_ads.id"],
            name="fk_enable_tasks_ad_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["snapshot_id"], ["ad_snapshots.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["recommendation_event_id"],
            ["enable_recommendation_events.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_enable_tasks_ad_id", "enable_tasks", ["ad_id"])
    op.create_index("ix_enable_tasks_snapshot_id", "enable_tasks", ["snapshot_id"])
    op.create_index(
        "ix_enable_tasks_recommendation_event_id",
        "enable_tasks",
        ["recommendation_event_id"],
    )
    op.create_index("ix_enable_tasks_status", "enable_tasks", ["status"])
    op.create_index("ix_enable_tasks_next_retry_at", "enable_tasks", ["next_retry_at"])
    op.create_index("uq_enable_task_idempotency", "enable_tasks", ["idempotency_key"], unique=True)
    op.create_index("ix_enable_task_queue", "enable_tasks", ["status", "next_retry_at"])

    op.create_table(
        "vision_settings",
        _id_column(),
        *_timestamps(),
        sa.Column("singleton_key", sa.String(length=32), nullable=False),
        sa.Column("api_url", sa.String(length=255), nullable=False),
        sa.Column("x_token_encrypted", sa.Text(), nullable=False),
        sa.Column("profile_id", sa.String(length=128), nullable=False),
        sa.Column("reconnect_requested", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("singleton_key"),
    )

    op.create_table(
        "telegram_invites",
        _id_column(),
        *_timestamps(),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("created_by_telegram_user_id", sa.String(length=64), nullable=False),
        sa.Column("created_by_username", sa.String(length=255), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_telegram_invites_code", "telegram_invites", ["code"], unique=True)
    op.create_index("ix_telegram_invites_expires_at", "telegram_invites", ["expires_at"])
    op.create_index("ix_telegram_invites_used_at", "telegram_invites", ["used_at"])
    op.create_index("ix_telegram_invites_revoked_at", "telegram_invites", ["revoked_at"])

    op.create_table(
        "telegram_recipients",
        _id_column(),
        *_timestamps(),
        sa.Column("chat_id", sa.String(length=64), nullable=False),
        sa.Column("telegram_user_id", sa.String(length=64), nullable=False),
        sa.Column("username", sa.String(length=128), nullable=False),
        sa.Column("first_name", sa.String(length=128), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_telegram_recipients_chat_id", "telegram_recipients", ["chat_id"])
    op.create_index(
        "ix_telegram_recipients_telegram_user_id",
        "telegram_recipients",
        ["telegram_user_id"],
    )
    op.create_index(
        "uq_telegram_recipients_chat_and_user",
        "telegram_recipients",
        ["chat_id", "telegram_user_id"],
        unique=True,
    )

    op.create_table(
        "telegram_message_refs",
        _id_column(),
        *_timestamps(),
        sa.Column("ad_id", sa.Uuid(), nullable=False),
        sa.Column("telegram_chat_id", sa.String(length=64), nullable=False),
        sa.Column("telegram_message_id", sa.Integer(), nullable=False),
        sa.Column("incident_key", sa.String(length=64), nullable=False),
        sa.Column("stream_kind", telegram_notification_stream_enum, nullable=False),
        sa.ForeignKeyConstraint(
            ["ad_id"],
            ["fb_ads.id"],
            name="fk_telegram_message_refs_ad_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_telegram_message_refs_ad_id", "telegram_message_refs", ["ad_id"])
    op.create_index(
        "ix_telegram_message_refs_telegram_chat_id",
        "telegram_message_refs",
        ["telegram_chat_id"],
    )
    op.create_index(
        "ix_telegram_message_refs_telegram_message_id",
        "telegram_message_refs",
        ["telegram_message_id"],
    )
    op.create_index(
        "ix_telegram_message_refs_incident_key",
        "telegram_message_refs",
        ["incident_key"],
    )
    op.create_index(
        "ix_telegram_message_refs_stream_kind", "telegram_message_refs", ["stream_kind"]
    )
    op.create_index(
        "uq_telegram_message_refs_stream",
        "telegram_message_refs",
        ["telegram_chat_id", "ad_id", "incident_key", "stream_kind"],
        unique=True,
    )


def downgrade() -> None:
    for table_name in (
        "telegram_message_refs",
        "telegram_recipients",
        "telegram_invites",
        "vision_settings",
        "enable_tasks",
        "ad_deposit_corrections",
        "enable_recommendation_events",
        "disable_tasks",
        "alert_events",
        "ad_snapshots",
        "ad_metric_history",
        "fb_ads",
        "offer_rule_configs",
        "offers",
        "telegram_settings",
        "cabinet_day_archives",
        "observer_settings",
    ):
        op.drop_table(table_name)
    _drop_enums()
