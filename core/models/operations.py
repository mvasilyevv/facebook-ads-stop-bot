from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Index, Numeric, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.db.base import Base
from core.domain import (
    ActionExecutionStatus,
    ActionJobStatus,
    ActionType,
    DecisionType,
    EntityType,
    RiskBand,
    ScanPipelineKind,
    ScanRunStatus,
    TelegramEventType,
    TrackingMode,
)
from core.models.base_mixins import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from core.models.advertising import Ad, MetricSnapshot
    from core.models.browser import BrowserHost, ProfileLaunch

_ENTITY_TYPE_ENUM = Enum(
    EntityType,
    name="entity_type_enum",
    values_callable=lambda enum_cls: [item.value for item in enum_cls],
)


class RuleSet(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "rule_sets"

    code: Mapped[str] = mapped_column(String(100), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(default=True)
    config_json: Mapped[dict] = mapped_column(JSON, default=dict)

    rules: Mapped[list["Rule"]] = relationship(back_populates="rule_set")


class Rule(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "rules"

    rule_set_id: Mapped[str] = mapped_column(ForeignKey("rule_sets.id", ondelete="CASCADE"))
    code: Mapped[str] = mapped_column(String(100), index=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(String(500))
    is_enabled: Mapped[bool] = mapped_column(default=True)
    config_json: Mapped[dict] = mapped_column(JSON, default=dict)

    rule_set: Mapped[RuleSet] = relationship(back_populates="rules")


class ScanRun(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "scan_runs"

    browser_host_id: Mapped[str | None] = mapped_column(
        ForeignKey("browser_hosts.id", ondelete="SET NULL")
    )
    profile_id: Mapped[str | None] = mapped_column(ForeignKey("profiles.id", ondelete="SET NULL"))
    profile_launch_id: Mapped[str | None] = mapped_column(
        ForeignKey("profile_launches.id", ondelete="SET NULL")
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pipeline_kind: Mapped[ScanPipelineKind] = mapped_column(
        Enum(ScanPipelineKind, name="scan_pipeline_kind_enum"),
        default=ScanPipelineKind.FULL_SCAN,
        index=True,
    )
    trigger_source: Mapped[str] = mapped_column(String(64), default="scheduler")
    target_fb_ad_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[ScanRunStatus] = mapped_column(Enum(ScanRunStatus, name="scan_run_status_enum"))
    rows_seen: Mapped[int] = mapped_column(default=0)
    rows_parsed: Mapped[int] = mapped_column(default=0)
    collect_ms: Mapped[int] = mapped_column(default=0)
    evaluate_ms: Mapped[int] = mapped_column(default=0)
    persist_ms: Mapped[int] = mapped_column(default=0)
    queue_ms: Mapped[int] = mapped_column(default=0)
    action_jobs_enqueued: Mapped[int] = mapped_column(default=0)
    scope_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    error_message: Mapped[str | None] = mapped_column(String(500))

    metric_snapshots: Mapped[list[MetricSnapshot]] = relationship(back_populates="scan_run")
    decisions: Mapped[list["Decision"]] = relationship(back_populates="scan_run")
    profile_launch: Mapped["ProfileLaunch | None"] = relationship(back_populates="scan_runs")


class ControlFlag(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "control_flags"

    entity_type: Mapped[EntityType] = mapped_column(_ENTITY_TYPE_ENUM)
    entity_id: Mapped[str] = mapped_column(String(255), index=True)
    tracking_mode: Mapped[TrackingMode] = mapped_column(
        Enum(TrackingMode, name="tracking_mode_enum"),
        default=TrackingMode.MANUAL_BLOCK,
    )
    reason: Mapped[str] = mapped_column(String(255))
    created_by: Mapped[str] = mapped_column(String(100))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Decision(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "decisions"
    __table_args__ = (Index("ix_decisions_scan_run_id", "scan_run_id"),)

    scan_run_id: Mapped[str] = mapped_column(ForeignKey("scan_runs.id", ondelete="CASCADE"))
    ad_id: Mapped[str | None] = mapped_column(ForeignKey("ads.id", ondelete="SET NULL"))
    fb_ad_id: Mapped[str] = mapped_column(String(64), index=True)
    rule_id: Mapped[str | None] = mapped_column(ForeignKey("rules.id", ondelete="SET NULL"))
    offer_id: Mapped[str | None] = mapped_column(ForeignKey("offers.id", ondelete="SET NULL"))
    offer_rate_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("offer_rate_versions.id", ondelete="SET NULL")
    )
    resolved_cpa_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    decision: Mapped[DecisionType] = mapped_column(Enum(DecisionType, name="decision_type_enum"))
    reason: Mapped[str] = mapped_column(String(500))
    action_executed: Mapped[bool] = mapped_column(default=False)
    action_status: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    scan_run: Mapped[ScanRun] = relationship(back_populates="decisions")
    ad: Mapped[Ad | None] = relationship(back_populates="decisions")
    telegram_events: Mapped[list["TelegramEvent"]] = relationship(back_populates="decision")


class ActionExecution(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "action_executions"

    decision_id: Mapped[str] = mapped_column(ForeignKey("decisions.id", ondelete="CASCADE"))
    action_type: Mapped[ActionType] = mapped_column(Enum(ActionType, name="action_type_enum"))
    status: Mapped[ActionExecutionStatus] = mapped_column(
        Enum(ActionExecutionStatus, name="action_execution_status_enum")
    )
    message: Mapped[str | None] = mapped_column(String(500))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WatchlistEntry(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "watchlist_entries"
    __table_args__ = (
        Index("ix_watchlist_entries_next_check_priority", "next_check_at", "priority_score"),
        Index("ix_watchlist_entries_profile_next_check", "profile_id", "next_check_at"),
    )

    ad_id: Mapped[str | None] = mapped_column(ForeignKey("ads.id", ondelete="SET NULL"))
    fb_ad_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    profile_id: Mapped[str | None] = mapped_column(ForeignKey("profiles.id", ondelete="SET NULL"))
    browser_host_id: Mapped[str | None] = mapped_column(
        ForeignKey("browser_hosts.id", ondelete="SET NULL")
    )
    risk_band: Mapped[RiskBand] = mapped_column(Enum(RiskBand, name="risk_band_enum"))
    priority_score: Mapped[int] = mapped_column(default=0)
    next_check_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_reason: Mapped[str | None] = mapped_column(String(500))
    last_metrics_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_scan_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("scan_runs.id", ondelete="SET NULL")
    )
    attempt_count: Mapped[int] = mapped_column(default=0)

    ad: Mapped["Ad | None"] = relationship()
    browser_host: Mapped["BrowserHost | None"] = relationship()
    source_scan_run: Mapped[ScanRun | None] = relationship()


class ActionJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "action_jobs"
    __table_args__ = (
        Index("ix_action_jobs_next_attempt_priority", "next_attempt_at", "priority_score"),
        Index("ix_action_jobs_profile_status_created", "profile_id", "status", "created_at"),
        Index(
            "uq_action_jobs_active_pause_per_ad",
            "fb_ad_id",
            "action_type",
            unique=True,
            sqlite_where=text("status IN ('QUEUED', 'RUNNING', 'RETRYING')"),
            postgresql_where=text("status IN ('QUEUED', 'RUNNING', 'RETRYING')"),
        ),
    )

    decision_id: Mapped[str | None] = mapped_column(ForeignKey("decisions.id", ondelete="SET NULL"))
    ad_id: Mapped[str | None] = mapped_column(ForeignKey("ads.id", ondelete="SET NULL"))
    fb_ad_id: Mapped[str] = mapped_column(String(64), index=True)
    profile_id: Mapped[str | None] = mapped_column(ForeignKey("profiles.id", ondelete="SET NULL"))
    browser_host_id: Mapped[str | None] = mapped_column(
        ForeignKey("browser_hosts.id", ondelete="SET NULL")
    )
    action_type: Mapped[ActionType] = mapped_column(Enum(ActionType, name="action_type_enum"))
    status: Mapped[ActionJobStatus] = mapped_column(
        Enum(ActionJobStatus, name="action_job_status_enum"),
        default=ActionJobStatus.QUEUED,
        index=True,
    )
    priority_score: Mapped[int] = mapped_column(default=0)
    attempt_count: Mapped[int] = mapped_column(default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_error: Mapped[str | None] = mapped_column(String(500))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    ad: Mapped["Ad | None"] = relationship()
    browser_host: Mapped["BrowserHost | None"] = relationship()
    decision: Mapped[Decision | None] = relationship()


class TelegramEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "telegram_events"

    decision_id: Mapped[str | None] = mapped_column(ForeignKey("decisions.id", ondelete="SET NULL"))
    event_type: Mapped[TelegramEventType] = mapped_column(
        Enum(TelegramEventType, name="telegram_event_type_enum")
    )
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(64))

    decision: Mapped[Decision | None] = relationship(back_populates="telegram_events")


class Cooldown(Base):
    __tablename__ = "cooldowns"

    entity_type: Mapped[EntityType] = mapped_column(
        _ENTITY_TYPE_ENUM,
        primary_key=True,
    )
    entity_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    until_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str] = mapped_column(String(255))


class SystemSetting(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Системные настройки, хранящиеся в базе данных."""

    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    value: Mapped[str] = mapped_column(String(1000))
    description: Mapped[str | None] = mapped_column(String(500))
