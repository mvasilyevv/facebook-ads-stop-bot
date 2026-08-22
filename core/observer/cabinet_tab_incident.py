# -*- coding: utf-8 -*-
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine

from core.telegram.worker_notify import notify_recurring_incident, resolve_recurring_incident

CABINET_TAB_UNAVAILABLE_INCIDENT_PREFIX = "observer:cabinet_tab_unavailable:"


async def sync_cabinet_tab_incident(
    engine: AsyncEngine,
    *,
    account_id: str,
    confirmed: bool,
) -> None:
    """Идемпотентно открывает или закрывает per-cabinet инцидент «вкладка не открыта».

    Один и тот же ключ используется observer и watchdog: при включённом сканировании
    incident-plane управляет observer, при выключенном — watchdog. Оба вызывают эту
    функцию, поэтому повторный тик не создаёт дубль.
    """
    incident_key = f"{CABINET_TAB_UNAVAILABLE_INCIDENT_PREFIX}{account_id}"
    if confirmed:
        await resolve_recurring_incident(
            engine,
            incident_key=incident_key,
            audience="all",
            summary=f"Вкладка кабинета {account_id} снова подтверждена.",
        )
        return
    await notify_recurring_incident(
        engine,
        incident_key=incident_key,
        audience="all",
        event_type="observer_cabinet_tab_unavailable",
        severity="critical",
        title="Кабинет не открыт в Ads Manager",
        summary=f"Кабинет {account_id}: сам открыть вкладку не получилось.",
        risk="Пока вкладки нет, скан и авто-стоп не работают",
        lines=["Открой кабинет в Ads Manager в Vision-профиле"],
        resource_type="ad_account",
        resource_id=account_id,
    )
