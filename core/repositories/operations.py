from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.domain import (
    ActionExecutionStatus,
    ActionType,
    DecisionType,
    EntityType,
    ScanRunStatus,
    TrackingMode,
)
from core.models.operations import ActionExecution, ControlFlag, Decision, ScanRun, SystemSetting
from core.repositories.base import AsyncRepository


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
        limit: int | None = None,
    ) -> list[Decision]:
        stmt = select(Decision).order_by(Decision.created_at.desc())
        if scan_run_id is not None:
            stmt = stmt.where(Decision.scan_run_id == scan_run_id)
        if fb_ad_id is not None:
            stmt = stmt.where(Decision.fb_ad_id == fb_ad_id)
        if limit is not None:
            stmt = stmt.limit(limit)
        result = await self.session.scalars(stmt)
        return list(result.all())

    async def get_clean_streak_count(self, fb_ad_id: str) -> int:
        """Считает количество последних подряд решений NO_ACTION/WOULD_RESUME для объявления."""

        stmt = (
            select(Decision.decision)
            .where(Decision.fb_ad_id == fb_ad_id)
            .order_by(Decision.created_at.desc())
            .limit(50)
        )
        result = await self.session.scalars(stmt)
        streak = 0
        for decision_type in result.all():
            if decision_type in {DecisionType.NO_ACTION, DecisionType.WOULD_RESUME}:
                streak += 1
                continue
            break
        return streak

    async def add_action_execution(
        self,
        decision_id: str,
        action_type: ActionType,
        status: ActionExecutionStatus,
        started_at: datetime,
        message: str | None = None,
        finished_at: datetime | None = None,
    ) -> ActionExecution:
        item = ActionExecution(
            decision_id=decision_id,
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
        finished_at: datetime | None = None,
        rows_seen: int = 0,
        rows_parsed: int = 0,
        scope_summary: dict | None = None,
        error_message: str | None = None,
    ) -> ScanRun:
        scan_run = ScanRun(
            browser_host_id=self._coerce_uuid(browser_host_id),
            profile_id=self._coerce_uuid(profile_id),
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            rows_seen=rows_seen,
            rows_parsed=rows_parsed,
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

    async def update_scan_run(
        self,
        scan_run_id: str,
        *,
        status: ScanRunStatus | None = None,
        finished_at: datetime | None = None,
        rows_seen: int | None = None,
        rows_parsed: int | None = None,
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
        if rows_seen is not None:
            scan_run.rows_seen = rows_seen
        if rows_parsed is not None:
            scan_run.rows_parsed = rows_parsed
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
