# -*- coding: utf-8 -*-
"""Unit-тесты CAS-инварианта FSM в UPSERT ad_snapshots.

UPSERT не должен откатывать терминальные состояния alert_state (DISABLED,
CLAIMED) обратно в нетерминальные (NORMAL, WARNING_SENT, STOP_SENT). FSM
наблюдателя — однонаправленная: терминальные записи может ставить только
disable_worker/enable_worker через mark_succeeded.
"""

from __future__ import annotations

import uuid as _uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

from core.domain import AlertState
from core.observer.snapshot_writer import _upsert_ad_snapshots


def _compile(stmt):
    """Скомпилировать стейтмент для postgres-диалекта."""
    return stmt.compile(dialect=postgresql.dialect())


def _capture_session() -> tuple[AsyncMock, list]:
    """Сессия-мок, сохраняющая все execute-стейтменты в список captured."""
    captured: list = []

    async def _capture(stmt, *args, **kwargs):
        captured.append(stmt)
        return None

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=_capture)
    return session, captured


def _build_row(*, fb_ad_id: str, alert_state: str) -> dict:
    """Минимальная строка для _upsert_ad_snapshots."""
    return {
        "fb_ad_id": fb_ad_id,
        "ad_id": _uuid.uuid4(),
        "alert_state": alert_state,
        "current_stage": None,
        "delivery_status": "ACTIVE",
        "last_observed_at": None,
        "last_scan_id": 1,
        "warning_rule_codes": [],
        "stop_rule_codes": [],
        "open_state_token": None,
        "telegram_group_key": None,
    }


# Проверяет что в SET UPSERT'а используется CASE-выражение, охраняющее
# терминальные состояния DISABLED/CLAIMED от перезаписи.
@pytest.mark.asyncio
async def test_upsert_emits_cas_for_alert_state() -> None:
    session, captured = _capture_session()
    rows = [_build_row(fb_ad_id="ad-1", alert_state="WARNING_SENT")]

    await _upsert_ad_snapshots(session, rows)

    assert captured, "UPSERT-стейтмент не был отправлен"
    compiled = _compile(captured[-1])
    sql = str(compiled)

    # CAS-инвариант: alert_state выставляется через CASE, а не прямо из excluded.
    assert "alert_state = CASE WHEN" in sql
    assert "ad_snapshots.alert_state IN" in sql
    assert "THEN ad_snapshots.alert_state" in sql
    assert "ELSE excluded.alert_state" in sql

    # IN-список — это [DISABLED, CLAIMED]; проверяем postcompile-параметры.
    in_lists = [
        v
        for v in compiled.params.values()
        if isinstance(v, list) and v and all(isinstance(x, AlertState) for x in v)
    ]
    assert in_lists, "не нашёл список значений для IN(...) в params"
    flat = {item for lst in in_lists for item in lst}
    assert AlertState.DISABLED in flat
    assert AlertState.CLAIMED in flat
    # Нетерминальные состояния не должны быть в защитном списке.
    assert AlertState.NORMAL not in flat
    assert AlertState.WARNING_SENT not in flat
    assert AlertState.STOP_SENT not in flat


# Проверяет что UPSERT не откатывает FSM из терминального состояния DISABLED
# обратно в WARNING_SENT при попытке observer'а перезаписать строку.
@pytest.mark.asyncio
async def test_upsert_does_not_overwrite_disabled_with_warning() -> None:
    session, captured = _capture_session()
    # observer пытается записать WARNING_SENT поверх DISABLED-записи в БД.
    rows = [_build_row(fb_ad_id="ad-disabled", alert_state="WARNING_SENT")]

    await _upsert_ad_snapshots(session, rows)

    sql = str(_compile(captured[-1]))
    # При DISABLED в БД CASE возвращает старое значение, а не WARNING_SENT из excluded.
    # Прямое присваивание `SET alert_state = excluded.alert_state` означало бы откат FSM.
    assert "SET alert_state = excluded.alert_state" not in sql
    assert "alert_state = CASE WHEN" in sql


# То же самое для CLAIMED — claim-состояние тоже терминальное.
@pytest.mark.asyncio
async def test_upsert_does_not_overwrite_claimed_with_normal() -> None:
    session, captured = _capture_session()
    rows = [_build_row(fb_ad_id="ad-claimed", alert_state="NORMAL")]

    await _upsert_ad_snapshots(session, rows)

    compiled = _compile(captured[-1])
    sql = str(compiled)
    assert "alert_state = CASE WHEN" in sql

    # В защитном IN-списке должен быть CLAIMED.
    in_lists = [
        v
        for v in compiled.params.values()
        if isinstance(v, list) and v and all(isinstance(x, AlertState) for x in v)
    ]
    flat = {item for lst in in_lists for item in lst}
    assert AlertState.CLAIMED in flat


# Проверяет что нетерминальные состояния (NORMAL/WARNING_SENT/STOP_SENT)
# по-прежнему перезаписываются — иначе наблюдатель не сможет двигать FSM вперёд.
@pytest.mark.asyncio
async def test_upsert_allows_overwrite_of_nonterminal_states() -> None:
    session, captured = _capture_session()
    rows = [_build_row(fb_ad_id="ad-warn", alert_state="STOP_SENT")]

    await _upsert_ad_snapshots(session, rows)

    sql = str(_compile(captured[-1]))
    # ELSE-ветка — excluded.alert_state — означает: если в БД нетерминал,
    # его перезаписываем новым значением.
    assert "ELSE excluded.alert_state" in sql
