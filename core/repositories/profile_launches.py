from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import case, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.domain import ActionExecutionStatus, DecisionType, EntityType, ScanRunStatus
from core.models import Ad, MetricSnapshot, ProfileLaunch
from core.models.browser import BrowserHost, Profile
from core.models.operations import ActionExecution, ControlFlag, Cooldown, Decision, ScanRun
from core.repositories.base import AsyncRepository


@dataclass(slots=True, frozen=True)
class ProfileLaunchContext:
    """Запуск истории вместе с профилем и browser host."""

    launch: ProfileLaunch
    profile: Profile
    browser_host: BrowserHost


@dataclass(slots=True, frozen=True)
class ProfileLaunchResetStats:
    """Сколько операционных артефактов очищено при старте нового запуска."""

    cleared_control_flags: int
    cleared_cooldowns: int


@dataclass(slots=True, frozen=True)
class ProfileLaunchDashboardSummary:
    """Краткая сводка по выбранному запуску."""

    total_ads: int
    active_ads: int
    paused_ads: int
    attention_ads: int
    spend_total: Decimal
    scans_count: int
    last_scan_at: datetime | None


@dataclass(slots=True, frozen=True)
class ProfileLaunchTrendPoint:
    """Одна точка временного ряда запуска."""

    timestamp: datetime
    value: Decimal


@dataclass(slots=True, frozen=True)
class ProfileLaunchDashboard:
    """Полный набор операторской аналитики по запуску."""

    current: ProfileLaunchDashboardSummary
    previous: ProfileLaunchDashboardSummary | None
    spend_series: tuple[ProfileLaunchTrendPoint, ...]
    attention_series: tuple[ProfileLaunchTrendPoint, ...]
    action_series: tuple[ProfileLaunchTrendPoint, ...]
    previous_launch: ProfileLaunchContext | None


class ProfileLaunchesRepository(AsyncRepository):
    """Репозиторий запусков истории профиля и их операторской аналитики."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    @staticmethod
    def _restore_utc(value: datetime | None) -> datetime | None:
        """Возвращает datetime с UTC, если SQLite снял timezone."""
        if value is None or value.tzinfo is not None:
            return value
        return value.replace(tzinfo=UTC)

    async def get_profile_launch(self, launch_id: UUID | str) -> ProfileLaunch | None:
        return await self.session.get(ProfileLaunch, self._coerce_uuid(launch_id))

    async def get_profile_launch_context(
        self,
        launch_id: UUID | str,
    ) -> ProfileLaunchContext | None:
        stmt = (
            select(ProfileLaunch, Profile, BrowserHost)
            .join(Profile, ProfileLaunch.profile_id == Profile.id)
            .join(BrowserHost, Profile.browser_host_id == BrowserHost.id)
            .where(ProfileLaunch.id == self._coerce_uuid(launch_id))
        )
        result = await self.session.execute(stmt)
        row = result.first()
        if row is None:
            return None
        launch, profile, browser_host = row
        return ProfileLaunchContext(launch=launch, profile=profile, browser_host=browser_host)

    async def list_profile_launches(self, profile_id: UUID | str) -> list[ProfileLaunch]:
        result = await self.session.scalars(
            select(ProfileLaunch)
            .where(ProfileLaunch.profile_id == self._coerce_uuid(profile_id))
            .order_by(ProfileLaunch.is_active.desc(), ProfileLaunch.started_at.desc())
        )
        return list(result.all())

    async def get_active_profile_launch(self, profile_id: UUID | str) -> ProfileLaunch | None:
        result = await self.session.scalars(
            select(ProfileLaunch)
            .where(
                ProfileLaunch.profile_id == self._coerce_uuid(profile_id),
                ProfileLaunch.is_active.is_(True),
            )
            .order_by(ProfileLaunch.started_at.desc())
            .limit(1)
        )
        return result.first()

    async def ensure_active_profile_launch(self, profile_id: UUID | str) -> ProfileLaunch:
        existing = await self.get_active_profile_launch(profile_id)
        if existing is not None:
            return existing
        now = datetime.now(tz=UTC)
        launch = ProfileLaunch(
            profile_id=self._coerce_uuid(profile_id),
            name=self._build_default_launch_name(now),
            is_active=True,
            started_at=now,
            ended_at=None,
        )
        self.session.add(launch)
        await self.session.flush()
        return launch

    async def rename_profile_launch(self, launch_id: UUID | str, name: str) -> ProfileLaunch | None:
        launch = await self.get_profile_launch(launch_id)
        if launch is None:
            return None
        launch.name = name.strip()
        await self.session.flush()
        return launch

    async def start_new_profile_launch(
        self,
        profile_id: UUID | str,
        *,
        name: str | None = None,
    ) -> tuple[ProfileLaunch, ProfileLaunchResetStats]:
        now = datetime.now(tz=UTC)
        profile_uuid = self._coerce_uuid(profile_id)
        current = await self.get_active_profile_launch(profile_uuid)
        if current is not None:
            current.is_active = False
            current.ended_at = now

        launch = ProfileLaunch(
            profile_id=profile_uuid,
            name=(name or "").strip() or self._build_default_launch_name(now),
            is_active=True,
            started_at=now,
            ended_at=None,
        )
        self.session.add(launch)
        await self.session.flush()

        reset_stats = await self._clear_profile_operational_state(profile_uuid)
        return launch, reset_stats

    async def build_dashboard(self, launch_id: UUID | str) -> ProfileLaunchDashboard | None:
        context = await self.get_profile_launch_context(launch_id)
        if context is None:
            return None

        launches = await self.list_profile_launches(context.profile.id)
        previous_launch = self._resolve_previous_launch(launches, context.launch)
        previous_context = (
            await self.get_profile_launch_context(previous_launch.id)
            if previous_launch is not None
            else None
        )
        current_summary = await self._build_summary(context.launch.id)
        previous_summary = (
            await self._build_summary(previous_launch.id) if previous_launch is not None else None
        )
        spend_series = await self._build_metric_series(context.launch.id, series="spend")
        attention_series = await self._build_metric_series(context.launch.id, series="attention")
        action_series = await self._build_metric_series(context.launch.id, series="actions")
        return ProfileLaunchDashboard(
            current=current_summary,
            previous=previous_summary,
            spend_series=tuple(spend_series),
            attention_series=tuple(attention_series),
            action_series=tuple(action_series),
            previous_launch=previous_context,
        )

    async def _build_summary(self, launch_id: UUID | str) -> ProfileLaunchDashboardSummary:
        launch_uuid = self._coerce_uuid(launch_id)
        latest_scan = await self.session.scalar(
            select(ScanRun)
            .where(
                ScanRun.profile_launch_id == launch_uuid,
                ScanRun.status == ScanRunStatus.SUCCEEDED,
            )
            .order_by(func.coalesce(ScanRun.finished_at, ScanRun.started_at).desc())
            .limit(1)
        )
        scans_count = int(
            await self.session.scalar(
                select(func.count())
                .select_from(ScanRun)
                .where(ScanRun.profile_launch_id == launch_uuid)
            )
            or 0
        )
        latest_scope = latest_scan.scope_summary if latest_scan is not None else {}
        active_ads = int((latest_scope or {}).get("active_rows") or 0)
        paused_ads = int((latest_scope or {}).get("paused_rows") or 0)
        last_scan_at = (
            latest_scan.finished_at or latest_scan.started_at if latest_scan is not None else None
        )
        last_scan_at = self._restore_utc(last_scan_at)

        latest_snapshots = await self._list_latest_snapshots_for_launch(
            launch_uuid,
            successful_only=True,
        )
        total_ads = len(latest_snapshots)
        spend_total = sum((snapshot.spend or Decimal("0")) for snapshot in latest_snapshots)

        latest_decisions = await self._list_latest_decisions_for_launch(
            launch_uuid,
            successful_only=True,
        )
        attention_ads = sum(
            1
            for decision in latest_decisions
            if decision.decision in {DecisionType.WOULD_PAUSE, DecisionType.ALERT_REJECTION}
            or (decision.action_status or "").upper() == "FAILED"
        )

        return ProfileLaunchDashboardSummary(
            total_ads=total_ads,
            active_ads=active_ads,
            paused_ads=paused_ads,
            attention_ads=attention_ads,
            spend_total=spend_total,
            scans_count=scans_count,
            last_scan_at=last_scan_at,
        )

    async def _build_metric_series(
        self,
        launch_id: UUID | str,
        *,
        series: str,
    ) -> list[ProfileLaunchTrendPoint]:
        launch_uuid = self._coerce_uuid(launch_id)
        scan_runs = list(
            (
                await self.session.scalars(
                    select(ScanRun)
                    .where(
                        ScanRun.profile_launch_id == launch_uuid,
                        ScanRun.status == ScanRunStatus.SUCCEEDED,
                    )
                    .order_by(ScanRun.started_at.asc(), ScanRun.id.asc())
                )
            ).all()
        )
        if not scan_runs:
            return []

        scan_run_ids = [scan_run.id for scan_run in scan_runs]
        if series == "spend":
            rows = (
                await self.session.execute(
                    select(
                        MetricSnapshot.scan_run_id, func.coalesce(func.sum(MetricSnapshot.spend), 0)
                    )
                    .where(MetricSnapshot.scan_run_id.in_(scan_run_ids))
                    .group_by(MetricSnapshot.scan_run_id)
                )
            ).all()
            values_map = {scan_run_id: Decimal(str(value or 0)) for scan_run_id, value in rows}
        elif series == "attention":
            rows = (
                await self.session.execute(
                    select(
                        Decision.scan_run_id,
                        func.coalesce(
                            func.sum(
                                case(
                                    (
                                        Decision.decision.in_(
                                            [
                                                DecisionType.WOULD_PAUSE,
                                                DecisionType.ALERT_REJECTION,
                                            ]
                                        ),
                                        1,
                                    ),
                                    else_=0,
                                )
                            ),
                            0,
                        ),
                    )
                    .where(Decision.scan_run_id.in_(scan_run_ids))
                    .group_by(Decision.scan_run_id)
                )
            ).all()
            values_map = {scan_run_id: Decimal(str(value or 0)) for scan_run_id, value in rows}
        else:
            rows = (
                await self.session.execute(
                    select(
                        Decision.scan_run_id,
                        func.coalesce(
                            func.sum(
                                case(
                                    (
                                        ActionExecution.status == ActionExecutionStatus.SUCCEEDED,
                                        1,
                                    ),
                                    else_=0,
                                )
                            ),
                            0,
                        ),
                    )
                    .join(ActionExecution, ActionExecution.decision_id == Decision.id, isouter=True)
                    .where(Decision.scan_run_id.in_(scan_run_ids))
                    .group_by(Decision.scan_run_id)
                )
            ).all()
            values_map = {scan_run_id: Decimal(str(value or 0)) for scan_run_id, value in rows}

        return [
            ProfileLaunchTrendPoint(
                timestamp=self._restore_utc(scan_run.finished_at or scan_run.started_at),
                value=values_map.get(scan_run.id, Decimal("0")),
            )
            for scan_run in scan_runs
        ]

    async def _list_latest_snapshots_for_launch(
        self,
        launch_id: UUID | str,
        *,
        successful_only: bool = False,
    ) -> list[MetricSnapshot]:
        ranked_snapshots_query = (
            select(
                MetricSnapshot.id.label("snapshot_id"),
                MetricSnapshot.fb_ad_id.label("fb_ad_id"),
                func.row_number()
                .over(
                    partition_by=MetricSnapshot.fb_ad_id,
                    order_by=(MetricSnapshot.captured_at.desc(), MetricSnapshot.id.desc()),
                )
                .label("row_number"),
            )
            .join(ScanRun, MetricSnapshot.scan_run_id == ScanRun.id)
            .where(ScanRun.profile_launch_id == self._coerce_uuid(launch_id))
        )
        if successful_only:
            ranked_snapshots_query = ranked_snapshots_query.where(
                ScanRun.status == ScanRunStatus.SUCCEEDED
            )
        ranked_snapshots = ranked_snapshots_query.subquery()
        result = await self.session.scalars(
            select(MetricSnapshot)
            .join(ranked_snapshots, MetricSnapshot.id == ranked_snapshots.c.snapshot_id)
            .where(ranked_snapshots.c.row_number == 1)
        )
        return list(result.all())

    async def _list_latest_decisions_for_launch(
        self,
        launch_id: UUID | str,
        *,
        successful_only: bool = False,
    ) -> list[Decision]:
        ranked_decisions_query = (
            select(
                Decision.id.label("decision_id"),
                Decision.fb_ad_id.label("fb_ad_id"),
                func.row_number()
                .over(
                    partition_by=Decision.fb_ad_id,
                    order_by=(Decision.created_at.desc(), Decision.id.desc()),
                )
                .label("row_number"),
            )
            .join(ScanRun, Decision.scan_run_id == ScanRun.id)
            .where(ScanRun.profile_launch_id == self._coerce_uuid(launch_id))
        )
        if successful_only:
            ranked_decisions_query = ranked_decisions_query.where(
                ScanRun.status == ScanRunStatus.SUCCEEDED
            )
        ranked_decisions = ranked_decisions_query.subquery()
        result = await self.session.scalars(
            select(Decision)
            .join(ranked_decisions, Decision.id == ranked_decisions.c.decision_id)
            .where(ranked_decisions.c.row_number == 1)
        )
        return list(result.all())

    async def _clear_profile_operational_state(
        self,
        profile_id: UUID | str,
    ) -> ProfileLaunchResetStats:
        profile_uuid = self._coerce_uuid(profile_id)
        profile_scan_run_ids = (
            select(ScanRun.id).where(ScanRun.profile_id == profile_uuid).scalar_subquery()
        )
        ads = list(
            (
                await self.session.scalars(
                    select(Ad)
                    .where(
                        or_(
                            Ad.last_scan_run_id.in_(profile_scan_run_ids),
                            Ad.id.in_(
                                select(MetricSnapshot.ad_id).where(
                                    MetricSnapshot.scan_run_id.in_(profile_scan_run_ids),
                                    MetricSnapshot.ad_id.is_not(None),
                                )
                            ),
                            Ad.fb_ad_id.in_(
                                select(MetricSnapshot.fb_ad_id).where(
                                    MetricSnapshot.scan_run_id.in_(profile_scan_run_ids)
                                )
                            ),
                            Ad.fb_ad_id.in_(
                                select(Decision.fb_ad_id).where(
                                    Decision.scan_run_id.in_(profile_scan_run_ids)
                                )
                            ),
                        )
                    )
                    .distinct()
                )
            ).all()
        )
        if not ads:
            return ProfileLaunchResetStats(cleared_control_flags=0, cleared_cooldowns=0)

        identifiers = {str(ad.id) for ad in ads}
        identifiers.update(ad.fb_ad_id for ad in ads)

        control_flags_result = await self.session.execute(
            delete(ControlFlag).where(
                ControlFlag.entity_type == EntityType.AD,
                ControlFlag.entity_id.in_(identifiers),
            )
        )
        cooldowns_result = await self.session.execute(
            delete(Cooldown).where(
                Cooldown.entity_type == EntityType.AD,
                Cooldown.entity_id.in_(identifiers),
            )
        )
        return ProfileLaunchResetStats(
            cleared_control_flags=int(control_flags_result.rowcount or 0),
            cleared_cooldowns=int(cooldowns_result.rowcount or 0),
        )

    @staticmethod
    def _build_default_launch_name(now: datetime) -> str:
        local_now = now.astimezone()
        return f"Запуск {local_now:%d.%m.%Y %H:%M}"

    @staticmethod
    def _resolve_previous_launch(
        launches: list[ProfileLaunch],
        current_launch: ProfileLaunch,
    ) -> ProfileLaunch | None:
        if current_launch.is_active:
            return next((item for item in launches if item.id != current_launch.id), None)
        archived_launches = sorted(
            (
                item
                for item in launches
                if item.id != current_launch.id and item.started_at <= current_launch.started_at
            ),
            key=lambda item: item.started_at,
            reverse=True,
        )
        return archived_launches[0] if archived_launches else None

    @staticmethod
    def _coerce_uuid(value: UUID | str) -> UUID:
        if isinstance(value, UUID):
            return value
        return UUID(str(value))
