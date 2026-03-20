"""Начальная схема проекта."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260320_0001"
down_revision = None
branch_labels = None
depends_on = None


tracking_mode_enum = sa.Enum(
    "TRACKED",
    "MANUAL_BLOCK",
    "READ_ONLY",
    "ARCHIVED",
    name="tracking_mode_enum",
)
delivery_status_enum = sa.Enum(
    "ACTIVE",
    "LEARNING",
    "PAUSED",
    "NOT_DELIVERING",
    "UNKNOWN",
    name="delivery_status_enum",
)
scope_presence_enum = sa.Enum(
    "IN_SCOPE",
    "NOT_SEEN_THIS_SCAN",
    "OUT_OF_SCOPE_CONFIRMED",
    name="scope_presence_enum",
)
decision_type_enum = sa.Enum(
    "NO_ACTION",
    "WOULD_PAUSE",
    "WOULD_RESUME",
    "SKIPPED_BY_POLICY",
    "INSUFFICIENT_DATA",
    "AMBIGUOUS",
    "ALERT_REJECTION",
    "KEPT_PAUSED_BY_VIABILITY",
    name="decision_type_enum",
)
entity_type_enum = sa.Enum("campaign", "adset", "ad", name="entity_type_enum")
scan_run_status_enum = sa.Enum(
    "PENDING", "RUNNING", "SUCCEEDED", "FAILED", "INVALID", name="scan_run_status_enum"
)
action_type_enum = sa.Enum("PAUSE", "RESUME", name="action_type_enum")
action_execution_status_enum = sa.Enum(
    "PENDING",
    "SUCCEEDED",
    "FAILED",
    "SKIPPED",
    name="action_execution_status_enum",
)
telegram_event_type_enum = sa.Enum(
    "AD_PAUSED_BY_BOT",
    "AD_RESUMED_BY_BOT",
    "AD_REJECTED_OR_NOT_DELIVERING",
    "OBSERVE_WOULD_PAUSE",
    "OBSERVE_WOULD_RESUME",
    "WORKER_ERROR",
    "SCOPE_INVALID",
    name="telegram_event_type_enum",
)


def upgrade() -> None:
    bind = op.get_bind()
    tracking_mode_enum.create(bind, checkfirst=True)
    delivery_status_enum.create(bind, checkfirst=True)
    scope_presence_enum.create(bind, checkfirst=True)
    decision_type_enum.create(bind, checkfirst=True)
    entity_type_enum.create(bind, checkfirst=True)
    scan_run_status_enum.create(bind, checkfirst=True)
    action_type_enum.create(bind, checkfirst=True)
    action_execution_status_enum.create(bind, checkfirst=True)
    telegram_event_type_enum.create(bind, checkfirst=True)

    op.create_table(
        "browser_hosts",
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("vendor", sa.String(length=64), nullable=False),
        sa.Column("api_base_url", sa.String(length=255), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_browser_hosts"),
        sa.UniqueConstraint("name", name="uq_browser_hosts_name"),
    )
    op.create_table(
        "offers",
        sa.Column("code", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_offers"),
        sa.UniqueConstraint("code", name="uq_offers_code"),
    )
    op.create_table(
        "rule_sets",
        sa.Column("code", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("config_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_rule_sets"),
        sa.UniqueConstraint("code", name="uq_rule_sets_code"),
    )
    op.create_table(
        "campaigns",
        sa.Column("fb_campaign_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("tracking_mode", tracking_mode_enum, nullable=False, server_default="TRACKED"),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_campaigns"),
        sa.UniqueConstraint("fb_campaign_id", name="uq_campaigns_fb_campaign_id"),
    )
    op.create_index("ix_campaigns_fb_campaign_id", "campaigns", ["fb_campaign_id"])
    op.create_table(
        "profiles",
        sa.Column("browser_host_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("vendor_profile_id", sa.String(length=128), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("last_launch_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["browser_host_id"],
            ["browser_hosts.id"],
            name="fk_profiles_browser_host_id_browser_hosts",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_profiles"),
    )
    op.create_index("ix_profiles_vendor_profile_id", "profiles", ["vendor_profile_id"])
    op.create_table(
        "offer_rate_versions",
        sa.Column("offer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cpa_usd", sa.Numeric(10, 2), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.String(length=255), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["offer_id"],
            ["offers.id"],
            name="fk_offer_rate_versions_offer_id_offers",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_offer_rate_versions"),
    )
    op.create_index(
        "ix_offer_rate_versions_effective_from", "offer_rate_versions", ["effective_from"]
    )
    op.create_index("ix_offer_rate_versions_effective_to", "offer_rate_versions", ["effective_to"])
    op.create_table(
        "rules",
        sa.Column("rule_set_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("config_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["rule_set_id"],
            ["rule_sets.id"],
            name="fk_rules_rule_set_id_rule_sets",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_rules"),
    )
    op.create_index("ix_rules_code", "rules", ["code"])
    op.create_table(
        "scan_runs",
        sa.Column("browser_host_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("profile_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", scan_run_status_enum, nullable=False),
        sa.Column("rows_seen", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rows_parsed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("scope_summary", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["browser_host_id"],
            ["browser_hosts.id"],
            name="fk_scan_runs_browser_host_id_browser_hosts",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["profiles.id"],
            name="fk_scan_runs_profile_id_profiles",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_scan_runs"),
    )
    op.create_table(
        "worker_heartbeats",
        sa.Column("worker_name", sa.String(length=128), nullable=False),
        sa.Column("browser_host_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("details", sa.String(length=500), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["browser_host_id"],
            ["browser_hosts.id"],
            name="fk_worker_heartbeats_browser_host_id_browser_hosts",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_worker_heartbeats"),
        sa.UniqueConstraint("worker_name", name="uq_worker_heartbeats_worker_name"),
    )
    op.create_table(
        "adsets",
        sa.Column("fb_adset_id", sa.String(length=64), nullable=False),
        sa.Column("campaign_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("tracking_mode", tracking_mode_enum, nullable=False, server_default="TRACKED"),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["campaign_id"],
            ["campaigns.id"],
            name="fk_adsets_campaign_id_campaigns",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_adsets"),
        sa.UniqueConstraint("fb_adset_id", name="uq_adsets_fb_adset_id"),
    )
    op.create_index("ix_adsets_fb_adset_id", "adsets", ["fb_adset_id"])
    op.create_table(
        "browser_sessions",
        sa.Column("browser_host_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cdp_url", sa.String(length=255), nullable=True),
        sa.Column("webdriver_url", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["browser_host_id"],
            ["browser_hosts.id"],
            name="fk_browser_sessions_browser_host_id_browser_hosts",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["profiles.id"],
            name="fk_browser_sessions_profile_id_profiles",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_browser_sessions"),
    )
    op.create_table(
        "cooldowns",
        sa.Column("entity_type", entity_type_enum, nullable=False),
        sa.Column("entity_id", sa.String(length=64), nullable=False),
        sa.Column("until_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.String(length=255), nullable=False),
        sa.PrimaryKeyConstraint("entity_type", "entity_id", name="pk_cooldowns"),
    )
    op.create_table(
        "ads",
        sa.Column("fb_ad_id", sa.String(length=64), nullable=False),
        sa.Column("campaign_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("adset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column(
            "delivery_status", delivery_status_enum, nullable=False, server_default="UNKNOWN"
        ),
        sa.Column("tracking_mode", tracking_mode_enum, nullable=False, server_default="TRACKED"),
        sa.Column(
            "scope_presence",
            scope_presence_enum,
            nullable=False,
            server_default="NOT_SEEN_THIS_SCAN",
        ),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_action_source", sa.String(length=64), nullable=True),
        sa.Column("last_action_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_decision", decision_type_enum, nullable=False, server_default="NO_ACTION"),
        sa.Column("last_scan_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["adset_id"], ["adsets.id"], name="fk_ads_adset_id_adsets", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["campaign_id"],
            ["campaigns.id"],
            name="fk_ads_campaign_id_campaigns",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["last_scan_run_id"],
            ["scan_runs.id"],
            name="fk_ads_last_scan_run_id_scan_runs",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_ads"),
        sa.UniqueConstraint("fb_ad_id", name="uq_ads_fb_ad_id"),
    )
    op.create_index("ix_ads_fb_ad_id", "ads", ["fb_ad_id"])
    op.create_index("ix_ads_last_scan_run_id", "ads", ["last_scan_run_id"])
    op.create_index("ix_ads_last_seen_at", "ads", ["last_seen_at"])
    op.create_index("ix_ads_tracking_mode", "ads", ["tracking_mode"])
    op.create_table(
        "control_flags",
        sa.Column("entity_type", entity_type_enum, nullable=False),
        sa.Column("entity_id", sa.String(length=64), nullable=False),
        sa.Column(
            "tracking_mode", tracking_mode_enum, nullable=False, server_default="MANUAL_BLOCK"
        ),
        sa.Column("reason", sa.String(length=255), nullable=False),
        sa.Column("created_by", sa.String(length=100), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_control_flags"),
    )
    op.create_index("ix_control_flags_entity_id", "control_flags", ["entity_id"])
    op.create_table(
        "entity_offer_bindings",
        sa.Column("entity_type", entity_type_enum, nullable=False),
        sa.Column("entity_id", sa.String(length=64), nullable=False),
        sa.Column("offer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("adset_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("ad_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["ad_id"], ["ads.id"], name="fk_entity_offer_bindings_ad_id_ads", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["adset_id"],
            ["adsets.id"],
            name="fk_entity_offer_bindings_adset_id_adsets",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["offer_id"],
            ["offers.id"],
            name="fk_entity_offer_bindings_offer_id_offers",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_entity_offer_bindings"),
    )
    op.create_index("ix_entity_offer_bindings_entity_id", "entity_offer_bindings", ["entity_id"])
    op.create_table(
        "decisions",
        sa.Column("scan_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ad_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("fb_ad_id", sa.String(length=64), nullable=False),
        sa.Column("rule_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("offer_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("offer_rate_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("resolved_cpa_usd", sa.Numeric(10, 2), nullable=True),
        sa.Column("decision", decision_type_enum, nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("action_executed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("action_status", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["ad_id"], ["ads.id"], name="fk_decisions_ad_id_ads", ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["offer_id"], ["offers.id"], name="fk_decisions_offer_id_offers", ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["offer_rate_version_id"],
            ["offer_rate_versions.id"],
            name="fk_decisions_offer_rate_version_id_offer_rate_versions",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["rule_id"], ["rules.id"], name="fk_decisions_rule_id_rules", ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["scan_run_id"],
            ["scan_runs.id"],
            name="fk_decisions_scan_run_id_scan_runs",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_decisions"),
    )
    op.create_index("ix_decisions_fb_ad_id", "decisions", ["fb_ad_id"])
    op.create_index("ix_decisions_scan_run_id", "decisions", ["scan_run_id"])
    op.create_table(
        "metric_snapshots",
        sa.Column("fb_ad_id", sa.String(length=64), nullable=False),
        sa.Column("ad_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("scan_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("offer_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("offer_rate_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("resolved_cpa_usd", sa.Numeric(10, 2), nullable=True),
        sa.Column("spend", sa.Numeric(10, 2), nullable=True),
        sa.Column("clicks", sa.Integer(), nullable=True),
        sa.Column("cpc", sa.Numeric(10, 2), nullable=True),
        sa.Column("leads", sa.Integer(), nullable=True),
        sa.Column("cost_per_lead", sa.Numeric(10, 2), nullable=True),
        sa.Column("registrations", sa.Integer(), nullable=True),
        sa.Column("cost_per_registration", sa.Numeric(10, 2), nullable=True),
        sa.Column("deposits", sa.Integer(), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["ad_id"], ["ads.id"], name="fk_metric_snapshots_ad_id_ads", ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["offer_id"],
            ["offers.id"],
            name="fk_metric_snapshots_offer_id_offers",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["offer_rate_version_id"],
            ["offer_rate_versions.id"],
            name="fk_metric_snapshots_offer_rate_version_id_offer_rate_versions",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["scan_run_id"],
            ["scan_runs.id"],
            name="fk_metric_snapshots_scan_run_id_scan_runs",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_metric_snapshots"),
    )
    op.create_index("ix_metric_snapshots_fb_ad_id", "metric_snapshots", ["fb_ad_id"])
    op.create_index("ix_metric_snapshots_scan_run_id", "metric_snapshots", ["scan_run_id"])
    op.create_table(
        "action_executions",
        sa.Column("decision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action_type", action_type_enum, nullable=False),
        sa.Column("status", action_execution_status_enum, nullable=False),
        sa.Column("message", sa.String(length=500), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["decision_id"],
            ["decisions.id"],
            name="fk_action_executions_decision_id_decisions",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_action_executions"),
    )
    op.create_table(
        "telegram_events",
        sa.Column("decision_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("event_type", telegram_event_type_enum, nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["decision_id"],
            ["decisions.id"],
            name="fk_telegram_events_decision_id_decisions",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_telegram_events"),
    )


def downgrade() -> None:
    op.drop_table("telegram_events")
    op.drop_table("action_executions")
    op.drop_index("ix_metric_snapshots_scan_run_id", table_name="metric_snapshots")
    op.drop_index("ix_metric_snapshots_fb_ad_id", table_name="metric_snapshots")
    op.drop_table("metric_snapshots")
    op.drop_index("ix_decisions_scan_run_id", table_name="decisions")
    op.drop_index("ix_decisions_fb_ad_id", table_name="decisions")
    op.drop_table("decisions")
    op.drop_index("ix_entity_offer_bindings_entity_id", table_name="entity_offer_bindings")
    op.drop_table("entity_offer_bindings")
    op.drop_index("ix_control_flags_entity_id", table_name="control_flags")
    op.drop_table("control_flags")
    op.drop_index("ix_ads_tracking_mode", table_name="ads")
    op.drop_index("ix_ads_last_seen_at", table_name="ads")
    op.drop_index("ix_ads_last_scan_run_id", table_name="ads")
    op.drop_index("ix_ads_fb_ad_id", table_name="ads")
    op.drop_table("ads")
    op.drop_table("cooldowns")
    op.drop_table("browser_sessions")
    op.drop_index("ix_adsets_fb_adset_id", table_name="adsets")
    op.drop_table("adsets")
    op.drop_table("worker_heartbeats")
    op.drop_table("scan_runs")
    op.drop_index("ix_rules_code", table_name="rules")
    op.drop_table("rules")
    op.drop_index("ix_offer_rate_versions_effective_to", table_name="offer_rate_versions")
    op.drop_index("ix_offer_rate_versions_effective_from", table_name="offer_rate_versions")
    op.drop_table("offer_rate_versions")
    op.drop_index("ix_profiles_vendor_profile_id", table_name="profiles")
    op.drop_table("profiles")
    op.drop_index("ix_campaigns_fb_campaign_id", table_name="campaigns")
    op.drop_table("campaigns")
    op.drop_table("rule_sets")
    op.drop_table("offers")
    op.drop_table("browser_hosts")

    bind = op.get_bind()
    telegram_event_type_enum.drop(bind, checkfirst=True)
    action_execution_status_enum.drop(bind, checkfirst=True)
    action_type_enum.drop(bind, checkfirst=True)
    scan_run_status_enum.drop(bind, checkfirst=True)
    entity_type_enum.drop(bind, checkfirst=True)
    decision_type_enum.drop(bind, checkfirst=True)
    scope_presence_enum.drop(bind, checkfirst=True)
    delivery_status_enum.drop(bind, checkfirst=True)
    tracking_mode_enum.drop(bind, checkfirst=True)
