from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.db.base import Base
from core.models.base_mixins import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from core.models.operations import ScanRun


class BrowserHost(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "browser_hosts"

    name: Mapped[str] = mapped_column(String(255), unique=True)
    vendor: Mapped[str] = mapped_column(String(64))
    api_base_url: Mapped[str] = mapped_column(String(255))
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    profiles: Mapped[list["Profile"]] = relationship(back_populates="browser_host")
    browser_sessions: Mapped[list["BrowserSession"]] = relationship(back_populates="browser_host")
    scan_runs: Mapped[list["ScanRun"]] = relationship()


class Profile(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "profiles"

    browser_host_id: Mapped[str] = mapped_column(ForeignKey("browser_hosts.id", ondelete="CASCADE"))
    vendor_profile_id: Mapped[str] = mapped_column(String(128), index=True)
    display_name: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    last_launch_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scan_suspended: Mapped[bool] = mapped_column(Boolean, default=False)
    scan_suspend_reason: Mapped[str | None] = mapped_column(String(500))
    scan_suspend_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    browser_host: Mapped[BrowserHost] = relationship(back_populates="profiles")
    browser_sessions: Mapped[list["BrowserSession"]] = relationship(back_populates="profile")
    scan_runs: Mapped[list["ScanRun"]] = relationship()


class BrowserSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "browser_sessions"

    browser_host_id: Mapped[str] = mapped_column(ForeignKey("browser_hosts.id", ondelete="CASCADE"))
    profile_id: Mapped[str] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"))
    cdp_url: Mapped[str | None] = mapped_column(String(255))
    webdriver_url: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(String(500))

    browser_host: Mapped[BrowserHost] = relationship(back_populates="browser_sessions")
    profile: Mapped[Profile] = relationship(back_populates="browser_sessions")


class WorkerHeartbeat(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "worker_heartbeats"

    worker_name: Mapped[str] = mapped_column(String(128), unique=True)
    browser_host_id: Mapped[str | None] = mapped_column(
        ForeignKey("browser_hosts.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(64))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    details: Mapped[str | None] = mapped_column(String(500))
