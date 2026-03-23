from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.domain import (
    ActionExecutionStatus,
    ActionJobStatus,
    ActionType,
    DecisionType,
    EntityType,
    RiskBand,
    ScanPipelineKind,
    ScanRunStatus,
    TrackingMode,
)
from core.models.browser import BrowserHost, Profile, ProfileLaunch
from core.models.operations import (
    ActionExecution,
    ActionJob,
    ControlFlag,
    Decision,
    ScanRun,
    SystemSetting,
    WatchlistEntry,
)
from core.repositories.base import AsyncRepository
from core.rules.evaluator import RESUME_REASON_INSUFFICIENT_CLEAN_STREAK

_ACTIVE_ACTION_JOB_STATUSES = (
    ActionJobStatus.QUEUED,
    ActionJobStatus.RUNNING,
    ActionJobStatus.RETRYING,
)


class DecisionsRepository(AsyncRepository):
    """Репозиторий для решений и действий."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def create_decision(
        self,
        scan_run_id: str,
        fb_ad_id: str,
        decision: DecisionType,
        reason: str,
        rule_id: str | None = None,
        ad_id: str | None = None,
        offer_id: str | None = None,
        offer_rate_version_id: str | None = None,
        resolved_cpa_usd: Decimal | None = None,
        action_executed: bool = False,
        action_status: str | None = None,
        created_at: datetime | None = None,
    ) -> Decision:
        item = Decision(
            scan_run_id=scan_run_id,
            fb_ad_id=fb_ad_id,
            decision=decision,
            reason=reason,
            rule_id=rule_id,
            ad_id=ad_id,
            offer_id=offer_id,
            offer_rate_version_id=offer_rate_version_id,
            resolved_cpa_usd=resolved_cpa_usd,
            action_executed=action_executed,
            action_status=action_status,
            created_at=created_at or datetime.now(tz=UTC),
        )
        self.session.add(item)
        await self.session.flush()
        return item

    async def list_decisions(
        self,
        scan_run_id: str | None = None,
        fb_ad_id: str | None = None,
        profile_id: UUID | str | None = None,
        profile_launch_id: UUID | str | None = None,
        limit: int | None = None,
    ) -> list[Decision]:
        stmt = select(Decision).order_by(Decision.created_at.desc())
        if scan_run_id is not None:
            stmt = stmt.where(Decision.scan_run_id == scan_run_id)
        if fb_ad_id is not None:
            stmt = stmt.where(Decision.fb_ad_id == fb_ad_id)
        if profile_id is not None or profile_launch_id is not None:
            stmt = stmt.join(ScanRun, Decision.scan_run_id == ScanRun.id)
        if profile_id is not None:
            stmt = stmt.where(ScanRun.profile_id == self._coerce_uuid(profile_id))
        if profile_launch_id is not None:
            stmt = stmt.where(ScanRun.profile_launch_id == self._coerce_uuid(profile_launch_id))
        if limit is not None:
            stmt = stmt.limit(limit)
        result = await self.session.scalars(stmt)
        return list(result.all())

    async def get_clean_streak_count(self, fb_ad_id: str) -> int:
        """Считает количество последних подряд чистых паузных решений для объявления."""

        stmt = (
            select(Decision.decision, Decision.reason)
            .where(Decision.fb_ad_id == fb_ad_id)
            .order_by(Decision.created_at.desc())
            .limit(50)
        )
        result = await self.session.execute(stmt)
        streak = 0
        for decision_type, reason in result.all():
            if decision_type == DecisionType.WOULD_RESUME:
                streak += 1
                continue
            if (
                decision_type == DecisionType.KEPT_PAUSED_BY_VIABILITY
                and reason == RESUME_REASON_INSUFFICIENT_CLEAN_STREAK
            ):
                streak += 1
                continue
            break
        return streak

    async def get_clean_streak_counts(self, fb_ad_ids: list[str]) -> dict[str, int]:
        """Возвращает счетчик чистых сканов для набора объявлений."""

        return {
            fb_ad_id: await self.get_clean_streak_count(fb_ad_id)
            for fb_ad_id in dict.fromkeys(fb_ad_id for fb_ad_id in fb_ad_ids if fb_ad_id)
        }

    async def get_latest_decisions(
        self,
        fb_ad_ids: list[str],
        profile_launch_id: UUID | str | None = None,
    ) -> dict[str, Decision]:
        """Возвращает последнее решение для каждого объявления из списка."""

        if not fb_ad_ids:
            return {}

        ranked_base = select(
            Decision.id.label("decision_id"),
            Decision.fb_ad_id.label("fb_ad_id"),
            func.row_number()
            .over(
                partition_by=Decision.fb_ad_id,
                order_by=(Decision.created_at.desc(), Decision.id.desc()),
            )
            .label("row_number"),
        )
        if profile_launch_id is not None:
            ranked_base = ranked_base.join(ScanRun, Decision.scan_run_id == ScanRun.id).where(
                ScanRun.profile_launch_id == self._coerce_uuid(profile_launch_id)
            )
        ranked_decisions = ranked_base.where(Decision.fb_ad_id.in_(fb_ad_ids)).subquery()

        result = await self.session.scalars(
            select(Decision)
            .join(
                ranked_decisions,
                Decision.id == ranked_decisions.c.decision_id,
            )
            .where(ranked_decisions.c.row_number == 1)
        )
        return {decision.fb_ad_id: decision for decision in result.all()}

    async def get_latest_action_executions(
        self,
        decision_ids: list[str],
    ) -> dict[str, ActionExecution]:
        """Возвращает последнее выполнение действия для каждого решения."""

        if not decision_ids:
            return {}
        normalized_decision_ids = [
            self._coerce_uuid(decision_id)
            for decision_id in decision_ids
            if decision_id is not None
        ]

        ranked_actions = (
            select(
                ActionExecution.id.label("action_execution_id"),
                ActionExecution.decision_id.label("decision_id"),
                func.row_number()
                .over(
                    partition_by=ActionExecution.decision_id,
                    order_by=(ActionExecution.started_at.desc(), ActionExecution.id.desc()),
                )
                .label("row_number"),
            )
            .where(ActionExecution.decision_id.in_(normalized_decision_ids))
            .subquery()
        )

        result = await self.session.scalars(
            select(ActionExecution)
            .join(
                ranked_actions,
                ActionExecution.id == ranked_actions.c.action_execution_id,
            )
            .where(ranked_actions.c.row_number == 1)
        )
        return {str(action.decision_id): action for action in result.all()}

    async def get_latest_successful_actions(
        self,
        fb_ad_ids: list[str],
    ) -> dict[str, tuple[ActionType, datetime]]:
        """Возвращает последнее успешное действие по каждому объявлению."""

        if not fb_ad_ids:
            return {}

        occurred_at = func.coalesce(ActionExecution.finished_at, ActionExecution.started_at)
        ranked_actions = (
            select(
                Decision.fb_ad_id.label("fb_ad_id"),
                ActionExecution.action_type.label("action_type"),
                occurred_at.label("occurred_at"),
                func.row_number()
                .over(
                    partition_by=Decision.fb_ad_id,
                    order_by=(occurred_at.desc(), ActionExecution.id.desc()),
                )
                .label("row_number"),
            )
            .join(Decision, Decision.id == ActionExecution.decision_id)
            .where(
                Decision.fb_ad_id.in_(fb_ad_ids),
                ActionExecution.status == ActionExecutionStatus.SUCCEEDED,
            )
            .subquery()
        )

        result = await self.session.execute(
            select(
                ranked_actions.c.fb_ad_id,
                ranked_actions.c.action_type,
                ranked_actions.c.occurred_at,
            ).where(ranked_actions.c.row_number == 1)
        )
        return {
            str(fb_ad_id): (action_type, occurred_at_value)
            for fb_ad_id, action_type, occurred_at_value in result.all()
            if occurred_at_value is not None
        }

    async def add_action_execution(
        self,
        decision_id: UUID | str,
        action_type: ActionType,
        status: ActionExecutionStatus,
        started_at: datetime,
        message: str | None = None,
        finished_at: datetime | None = None,
    ) -> ActionExecution:
        item = ActionExecution(
            decision_id=self._coerce_uuid(decision_id),
            action_type=action_type,
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            message=message,
        )
        self.session.add(item)
        await self.session.flush()
        return item

    async def set_decision_action_result(
        self,
        decision_id: str,
        *,
        action_executed: bool,
        action_status: str | None,
    ) -> Decision | None:
        """Обновляет результат выполнения действия у сохраненного решения."""

        decision = await self.session.get(Decision, self._coerce_uuid(decision_id))
        if decision is None:
            return None
        decision.action_executed = action_executed
        decision.action_status = action_status
        await self.session.flush()
        return decision

    @staticmethod
    def _coerce_uuid(value: UUID | str | None) -> UUID | None:
        if value is None or isinstance(value, UUID):
            return value
        return UUID(str(value))


class ControlFlagsRepository(AsyncRepository):
    """Репозиторий для флагов управления."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def upsert_control_flag(
        self,
        entity_type: EntityType,
        entity_id: str,
        reason: str,
        created_by: str,
        tracking_mode: TrackingMode,
        expires_at: datetime | None = None,
    ) -> ControlFlag:
        existing = await self.get_control_flag(entity_type, entity_id)
        if existing is None:
            flag = ControlFlag(
                entity_type=entity_type,
                entity_id=entity_id,
                reason=reason,
                created_by=created_by,
                tracking_mode=tracking_mode,
                expires_at=expires_at,
            )
            self.session.add(flag)
        else:
            existing.reason = reason
            existing.created_by = created_by
            existing.tracking_mode = tracking_mode
            existing.expires_at = expires_at
            flag = existing
        await self.session.flush()
        return flag

    async def get_control_flag(
        self,
        entity_type: EntityType,
        entity_id: str,
    ) -> ControlFlag | None:
        result = await self.session.scalars(
            select(ControlFlag).where(
                ControlFlag.entity_type == entity_type,
                ControlFlag.entity_id == entity_id,
            )
        )
        return result.first()

    async def list_control_flags(self) -> list[ControlFlag]:
        result = await self.session.scalars(
            select(ControlFlag).order_by(ControlFlag.created_at.desc())
        )
        return list(result.all())

    async def delete_control_flag(self, entity_type: EntityType, entity_id: str) -> bool:
        flag = await self.get_control_flag(entity_type, entity_id)
        if flag is None:
            return False
        await self.session.delete(flag)
        return True


class ScanRunsRepository(AsyncRepository):
    """Репозиторий для запусков сканов."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def create_scan_run(
        self,
        browser_host_id: UUID | str | None,
        profile_id: UUID | str | None,
        status: ScanRunStatus,
        started_at: datetime,
        *,
        profile_launch_id: UUID | str | None = None,
        pipeline_kind: ScanPipelineKind = ScanPipelineKind.FULL_SCAN,
        trigger_source: str = "scheduler",
        target_fb_ad_ids: list[str] | None = None,
        finished_at: datetime | None = None,
        rows_seen: int = 0,
        rows_parsed: int = 0,
        collect_ms: int = 0,
        evaluate_ms: int = 0,
        persist_ms: int = 0,
        queue_ms: int = 0,
        action_jobs_enqueued: int = 0,
        scope_summary: dict | None = None,
        error_message: str | None = None,
    ) -> ScanRun:
        scan_run = ScanRun(
            browser_host_id=self._coerce_uuid(browser_host_id),
            profile_id=self._coerce_uuid(profile_id),
            profile_launch_id=self._coerce_uuid(profile_launch_id),
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            pipeline_kind=pipeline_kind,
            trigger_source=trigger_source,
            target_fb_ad_ids=target_fb_ad_ids or [],
            rows_seen=rows_seen,
            rows_parsed=rows_parsed,
            collect_ms=collect_ms,
            evaluate_ms=evaluate_ms,
            persist_ms=persist_ms,
            queue_ms=queue_ms,
            action_jobs_enqueued=action_jobs_enqueued,
            scope_summary=scope_summary or {},
            error_message=error_message,
        )
        self.session.add(scan_run)
        await self.session.flush()
        return scan_run

    async def get_scan_run(self, scan_run_id: str) -> ScanRun | None:
        return await self.session.get(ScanRun, self._coerce_uuid(scan_run_id))

    async def list_scan_runs(self) -> list[ScanRun]:
        result = await self.session.scalars(select(ScanRun).order_by(ScanRun.started_at.desc()))
        return list(result.all())

    async def list_recent_scan_runs_for_profile(
        self,
        profile_id: UUID | str,
        *,
        limit: int = 10,
    ) -> list[ScanRun]:
        result = await self.session.scalars(
            select(ScanRun)
            .where(ScanRun.profile_id == self._coerce_uuid(profile_id))
            .order_by(ScanRun.started_at.desc())
            .limit(limit)
        )
        return list(result.all())

    async def list_scan_run_rows(
        self,
        *,
        profile_id: UUID | str | None = None,
        profile_launch_id: UUID | str | None = None,
    ) -> list[tuple[ScanRun, str | None, str | None, str | None, str | None]]:
        stmt = (
            select(
                ScanRun,
                BrowserHost.name,
                Profile.vendor_profile_id,
                ProfileLaunch.id,
                ProfileLaunch.name,
            )
            .outerjoin(BrowserHost, ScanRun.browser_host_id == BrowserHost.id)
            .outerjoin(Profile, ScanRun.profile_id == Profile.id)
            .outerjoin(ProfileLaunch, ScanRun.profile_launch_id == ProfileLaunch.id)
            .order_by(ScanRun.started_at.desc())
        )
        if profile_id is not None:
            stmt = stmt.where(Profile.vendor_profile_id == str(profile_id))
        if profile_launch_id is not None:
            stmt = stmt.where(ScanRun.profile_launch_id == self._coerce_uuid(profile_launch_id))
        result = await self.session.execute(stmt)
        return [
            (
                scan_run,
                browser_host_name,
                vendor_profile_id,
                str(profile_launch_id_value) if profile_launch_id_value is not None else None,
                profile_launch_name,
            )
            for scan_run, browser_host_name, vendor_profile_id, profile_launch_id_value, profile_launch_name in result.all()
        ]

    async def update_scan_run(
        self,
        scan_run_id: str,
        *,
        status: ScanRunStatus | None = None,
        finished_at: datetime | None = None,
        pipeline_kind: ScanPipelineKind | None = None,
        trigger_source: str | None = None,
        target_fb_ad_ids: list[str] | None = None,
        rows_seen: int | None = None,
        rows_parsed: int | None = None,
        collect_ms: int | None = None,
        evaluate_ms: int | None = None,
        persist_ms: int | None = None,
        queue_ms: int | None = None,
        action_jobs_enqueued: int | None = None,
        scope_summary: dict | None = None,
        error_message: str | None = None,
    ) -> ScanRun | None:
        scan_run = await self.get_scan_run(scan_run_id)
        if scan_run is None:
            return None
        if status is not None:
            scan_run.status = status
        if finished_at is not None:
            scan_run.finished_at = finished_at
        if pipeline_kind is not None:
            scan_run.pipeline_kind = pipeline_kind
        if trigger_source is not None:
            scan_run.trigger_source = trigger_source
        if target_fb_ad_ids is not None:
            scan_run.target_fb_ad_ids = list(target_fb_ad_ids)
        if rows_seen is not None:
            scan_run.rows_seen = rows_seen
        if rows_parsed is not None:
            scan_run.rows_parsed = rows_parsed
        if collect_ms is not None:
            scan_run.collect_ms = collect_ms
        if evaluate_ms is not None:
            scan_run.evaluate_ms = evaluate_ms
        if persist_ms is not None:
            scan_run.persist_ms = persist_ms
        if queue_ms is not None:
            scan_run.queue_ms = queue_ms
        if action_jobs_enqueued is not None:
            scan_run.action_jobs_enqueued = action_jobs_enqueued
        if scope_summary is not None:
            scan_run.scope_summary = scope_summary
        if error_message is not None:
            scan_run.error_message = error_message
        await self.session.flush()
        return scan_run

    @staticmethod
    def _coerce_uuid(value: UUID | str | None) -> UUID | None:
        if value is None or isinstance(value, UUID):
            return value
        return UUID(str(value))


class SystemSettingsRepository(AsyncRepository):
    """Репозиторий для системных настроек."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def get_setting(self, key: str) -> SystemSetting | None:
        """Получить значение системной настройки по ключу."""
        result = await self.session.scalars(select(SystemSetting).where(SystemSetting.key == key))
        return result.first()

    async def set_setting(
        self, key: str, value: str, description: str | None = None
    ) -> SystemSetting:
        """Установить или обновить системную настройку."""
        existing = await self.get_setting(key)
        if existing is None:
            setting = SystemSetting(key=key, value=value, description=description)
            self.session.add(setting)
        else:
            existing.value = value
            if description is not None:
                existing.description = description
            setting = existing
        await self.session.flush()
        return setting

    async def get_all_settings(self) -> dict[str, str]:
        """Получить все системные настройки как словарь."""
        result = await self.session.scalars(select(SystemSetting))
        return {setting.key: setting.value for setting in result.all()}


class WatchlistRepository(AsyncRepository):
    """Репозиторий watchlist записей для быстрого stop-контура."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def upsert_entry(
        self,
        *,
        ad_id: UUID | str | None,
        fb_ad_id: str,
        profile_id: UUID | str | None,
        browser_host_id: UUID | str | None,
        risk_band: RiskBand,
        priority_score: int,
        next_check_at: datetime,
        last_reason: str | None,
        last_metrics_at: datetime | None,
        source_scan_run_id: UUID | str | None,
    ) -> WatchlistEntry:
        entry = await self.get_entry_by_fb_ad_id(fb_ad_id)
        if entry is None:
            entry = WatchlistEntry(
                ad_id=self._coerce_uuid(ad_id),
                fb_ad_id=fb_ad_id,
                profile_id=self._coerce_uuid(profile_id),
                browser_host_id=self._coerce_uuid(browser_host_id),
                risk_band=risk_band,
                priority_score=priority_score,
                next_check_at=next_check_at,
                last_reason=last_reason,
                last_metrics_at=last_metrics_at,
                source_scan_run_id=self._coerce_uuid(source_scan_run_id),
                attempt_count=0,
            )
            self.session.add(entry)
        else:
            entry.ad_id = self._coerce_uuid(ad_id)
            entry.profile_id = self._coerce_uuid(profile_id)
            entry.browser_host_id = self._coerce_uuid(browser_host_id)
            entry.risk_band = risk_band
            entry.priority_score = priority_score
            entry.next_check_at = next_check_at
            entry.last_reason = last_reason
            entry.last_metrics_at = last_metrics_at
            entry.source_scan_run_id = self._coerce_uuid(source_scan_run_id)
        await self.session.flush()
        return entry

    async def get_entry_by_fb_ad_id(self, fb_ad_id: str) -> WatchlistEntry | None:
        result = await self.session.scalars(
            select(WatchlistEntry).where(WatchlistEntry.fb_ad_id == fb_ad_id)
        )
        return result.first()

    async def list_entries(self, *, limit: int | None = None) -> list[WatchlistEntry]:
        stmt = select(WatchlistEntry).order_by(
            WatchlistEntry.priority_score.desc(),
            WatchlistEntry.next_check_at.asc(),
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        result = await self.session.scalars(stmt)
        return list(result.all())

    async def get_entries_by_fb_ad_ids(self, fb_ad_ids: list[str]) -> dict[str, WatchlistEntry]:
        normalized_ids = [fb_ad_id for fb_ad_id in dict.fromkeys(fb_ad_ids) if fb_ad_id]
        if not normalized_ids:
            return {}
        result = await self.session.scalars(
            select(WatchlistEntry).where(WatchlistEntry.fb_ad_id.in_(normalized_ids))
        )
        return {entry.fb_ad_id: entry for entry in result.all()}

    async def list_ready_entries(
        self,
        *,
        limit: int = 50,
        now: datetime | None = None,
    ) -> list[WatchlistEntry]:
        effective_now = now or datetime.now(tz=UTC)
        result = await self.session.scalars(
            select(WatchlistEntry)
            .where(
                WatchlistEntry.next_check_at <= effective_now,
                WatchlistEntry.risk_band != RiskBand.SAFE,
            )
            .order_by(
                WatchlistEntry.priority_score.desc(),
                WatchlistEntry.next_check_at.asc(),
            )
            .limit(limit)
        )
        return list(result.all())

    async def schedule_retry(
        self,
        fb_ad_id: str,
        *,
        next_check_at: datetime,
        last_reason: str | None = None,
    ) -> WatchlistEntry | None:
        entry = await self.get_entry_by_fb_ad_id(fb_ad_id)
        if entry is None:
            return None
        entry.next_check_at = next_check_at
        entry.attempt_count += 1
        if last_reason is not None:
            entry.last_reason = last_reason
        await self.session.flush()
        return entry

    async def delete_entry(self, fb_ad_id: str) -> bool:
        entry = await self.get_entry_by_fb_ad_id(fb_ad_id)
        if entry is None:
            return False
        await self.session.delete(entry)
        return True

    async def delete_entries(self, fb_ad_ids: list[str]) -> int:
        normalized_ids = [fb_ad_id for fb_ad_id in fb_ad_ids if fb_ad_id]
        if not normalized_ids:
            return 0
        result = await self.session.execute(
            delete(WatchlistEntry).where(WatchlistEntry.fb_ad_id.in_(normalized_ids))
        )
        return int(result.rowcount or 0)

    @staticmethod
    def _coerce_uuid(value: UUID | str | None) -> UUID | None:
        if value is None or isinstance(value, UUID):
            return value
        return UUID(str(value))


class ActionJobsRepository(AsyncRepository):
    """Репозиторий очереди действий fast-stop контура."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def enqueue_action_job(
        self,
        *,
        decision_id: UUID | str | None,
        ad_id: UUID | str | None,
        fb_ad_id: str,
        profile_id: UUID | str | None,
        browser_host_id: UUID | str | None,
        action_type: ActionType,
        priority_score: int,
        next_attempt_at: datetime,
    ) -> ActionJob:
        existing = await self.get_active_job(fb_ad_id=fb_ad_id, action_type=action_type)
        if existing is None:
            existing = ActionJob(
                decision_id=self._coerce_uuid(decision_id),
                ad_id=self._coerce_uuid(ad_id),
                fb_ad_id=fb_ad_id,
                profile_id=self._coerce_uuid(profile_id),
                browser_host_id=self._coerce_uuid(browser_host_id),
                action_type=action_type,
                status=ActionJobStatus.QUEUED,
                priority_score=priority_score,
                attempt_count=0,
                next_attempt_at=next_attempt_at,
                last_error=None,
                started_at=None,
                finished_at=None,
            )
            self.session.add(existing)
        else:
            existing.decision_id = self._coerce_uuid(decision_id)
            existing.ad_id = self._coerce_uuid(ad_id)
            existing.profile_id = self._coerce_uuid(profile_id)
            existing.browser_host_id = self._coerce_uuid(browser_host_id)
            existing.priority_score = max(existing.priority_score, priority_score)
            existing.next_attempt_at = min(existing.next_attempt_at, next_attempt_at)
            existing.status = ActionJobStatus.QUEUED
            existing.last_error = None
        await self.session.flush()
        return existing

    async def get_active_job(
        self,
        *,
        fb_ad_id: str,
        action_type: ActionType,
    ) -> ActionJob | None:
        result = await self.session.scalars(
            select(ActionJob).where(
                ActionJob.fb_ad_id == fb_ad_id,
                ActionJob.action_type == action_type,
                ActionJob.status.in_(_ACTIVE_ACTION_JOB_STATUSES),
            )
        )
        return result.first()

    async def list_jobs(self, *, limit: int | None = None) -> list[ActionJob]:
        stmt = select(ActionJob).order_by(ActionJob.created_at.desc())
        if limit is not None:
            stmt = stmt.limit(limit)
        result = await self.session.scalars(stmt)
        return list(result.all())

    async def list_ready_jobs(
        self,
        *,
        now: datetime | None = None,
        limit: int = 50,
    ) -> list[ActionJob]:
        effective_now = now or datetime.now(tz=UTC)
        result = await self.session.scalars(
            select(ActionJob)
            .where(
                ActionJob.status.in_((ActionJobStatus.QUEUED, ActionJobStatus.RETRYING)),
                ActionJob.next_attempt_at <= effective_now,
            )
            .order_by(ActionJob.priority_score.desc(), ActionJob.created_at.asc())
            .limit(limit)
        )
        return list(result.all())

    async def get_latest_jobs(self, fb_ad_ids: list[str]) -> dict[str, ActionJob]:
        if not fb_ad_ids:
            return {}
        ranked_jobs = (
            select(
                ActionJob.id.label("action_job_id"),
                ActionJob.fb_ad_id.label("fb_ad_id"),
                func.row_number()
                .over(
                    partition_by=ActionJob.fb_ad_id,
                    order_by=(ActionJob.updated_at.desc(), ActionJob.id.desc()),
                )
                .label("row_number"),
            )
            .where(ActionJob.fb_ad_id.in_(fb_ad_ids))
            .subquery()
        )
        result = await self.session.scalars(
            select(ActionJob)
            .join(ranked_jobs, ActionJob.id == ranked_jobs.c.action_job_id)
            .where(ranked_jobs.c.row_number == 1)
        )
        return {job.fb_ad_id: job for job in result.all()}

    async def mark_running(
        self,
        job_id: UUID | str,
        *,
        started_at: datetime,
    ) -> ActionJob | None:
        job = await self.session.get(ActionJob, self._coerce_uuid(job_id))
        if job is None:
            return None
        job.status = ActionJobStatus.RUNNING
        job.started_at = started_at
        job.attempt_count += 1
        await self.session.flush()
        return job

    async def mark_succeeded(
        self,
        job_id: UUID | str,
        *,
        finished_at: datetime,
    ) -> ActionJob | None:
        job = await self.session.get(ActionJob, self._coerce_uuid(job_id))
        if job is None:
            return None
        job.status = ActionJobStatus.SUCCEEDED
        job.finished_at = finished_at
        job.last_error = None
        await self.session.flush()
        return job

    async def mark_failed(
        self,
        job_id: UUID | str,
        *,
        finished_at: datetime,
        error: str,
    ) -> ActionJob | None:
        job = await self.session.get(ActionJob, self._coerce_uuid(job_id))
        if job is None:
            return None
        job.status = ActionJobStatus.FAILED
        job.finished_at = finished_at
        job.last_error = error[:500]
        await self.session.flush()
        return job

    async def mark_retrying(
        self,
        job_id: UUID | str,
        *,
        next_attempt_at: datetime,
        error: str,
    ) -> ActionJob | None:
        job = await self.session.get(ActionJob, self._coerce_uuid(job_id))
        if job is None:
            return None
        job.status = ActionJobStatus.RETRYING
        job.next_attempt_at = next_attempt_at
        job.last_error = error[:500]
        job.finished_at = None
        await self.session.flush()
        return job

    @staticmethod
    def _coerce_uuid(value: UUID | str | None) -> UUID | None:
        if value is None or isinstance(value, UUID):
            return value
        return UUID(str(value))
