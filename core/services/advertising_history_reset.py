from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.models import (
    ActionExecution,
    Ad,
    AdSet,
    Campaign,
    ControlFlag,
    Cooldown,
    Decision,
    EntityOfferBinding,
    MetricSnapshot,
    ScanRun,
    TelegramEvent,
)


@dataclass(slots=True, frozen=True)
class AdvertisingHistoryResetTableStat:
    table_name: str
    rows: int


@dataclass(slots=True, frozen=True)
class AdvertisingHistoryResetReport:
    table_stats: tuple[AdvertisingHistoryResetTableStat, ...]
    is_dry_run: bool

    @property
    def total_rows(self) -> int:
        return sum(stat.rows for stat in self.table_stats)


_ADVERTISING_HISTORY_TABLES: tuple[tuple[str, type], ...] = (
    ("action_executions", ActionExecution),
    ("telegram_events", TelegramEvent),
    ("decisions", Decision),
    ("metric_snapshots", MetricSnapshot),
    ("entity_offer_bindings", EntityOfferBinding),
    ("control_flags", ControlFlag),
    ("cooldowns", Cooldown),
    ("ads", Ad),
    ("adsets", AdSet),
    ("campaigns", Campaign),
    ("scan_runs", ScanRun),
)


async def preview_advertising_history_reset(
    session: AsyncSession,
) -> AdvertisingHistoryResetReport:
    return await _build_reset_report(session=session, is_dry_run=True)


async def reset_advertising_history(
    session: AsyncSession,
) -> AdvertisingHistoryResetReport:
    report = await _build_reset_report(session=session, is_dry_run=False)
    try:
        for _, model in _ADVERTISING_HISTORY_TABLES:
            await session.execute(delete(model))
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    return report


async def _build_reset_report(
    session: AsyncSession,
    *,
    is_dry_run: bool,
) -> AdvertisingHistoryResetReport:
    table_stats: list[AdvertisingHistoryResetTableStat] = []
    for table_name, model in _ADVERTISING_HISTORY_TABLES:
        rows = await session.scalar(select(func.count()).select_from(model))
        table_stats.append(
            AdvertisingHistoryResetTableStat(
                table_name=table_name,
                rows=int(rows or 0),
            )
        )
    return AdvertisingHistoryResetReport(
        table_stats=tuple(table_stats),
        is_dry_run=is_dry_run,
    )
