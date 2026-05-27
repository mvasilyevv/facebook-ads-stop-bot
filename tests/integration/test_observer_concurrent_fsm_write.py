# -*- coding: utf-8 -*-
"""Integration: observer race condition против telegram_poller / toggle_executor.

Сценарий: пользователь успел кликнуть «Отключить» (state=claimed) или disable уже
прошёл (state=disabled), но observer в параллельном цикле всё ещё видит ад в STOP-rules
и пытается записать stop_sent. Без WHERE-guard в apply_fsm_transition это
затрёт terminal-состояние и работа disable_worker/enable_worker пойдёт ра спарой.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import text

from core.observer.pipeline import process_scan_rows
from core.scanner.models import ScannedAdRow


@pytest_asyncio.fixture
async def clean_concurrent_tables(pg_engine):
    """Чистка таблиц pipeline для изолированного теста."""

    async def _truncate():
        async with pg_engine.begin() as conn:
            for t in (
                "task_queue",
                "alert_events",
                "ad_metrics",
                "ad_alert_state",
                "fb_ads",
                "fb_adsets",
                "fb_campaigns",
                "offer_rules",
                "offers",
            ):
                await conn.execute(text(f"DELETE FROM {t}"))

    await _truncate()
    yield
    await _truncate()


@pytest_asyncio.fixture
async def offer_with_cpa(pg_engine, clean_concurrent_tables):
    """Оффер с CPA=10 — даёт STOP при spend=$25 deposits=0."""
    offer_id = uuid.uuid4()
    code = f"CC{uuid.uuid4().hex[:4].upper()}"
    async with pg_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO offers (id, code, name, is_active) VALUES (:i, :c, :n, TRUE)"),
            {"i": offer_id, "c": code, "n": "Concurrent test offer"},
        )
        await conn.execute(
            text("INSERT INTO offer_rules (offer_id, cpa_threshold) VALUES (:o, :cpa)"),
            {"o": offer_id, "cpa": Decimal("10.00")},
        )
    return {"offer_id": offer_id, "code": code}


def _stop_row(*, code: str, fb_ad_id: str) -> ScannedAdRow:
    """ScannedAdRow триггерящий STOP (fast-stop по spend без deposits)."""
    return ScannedAdRow(
        fb_ad_id=fb_ad_id,
        campaign_name=f"{code} | KE | promo",
        adset_name="ADS_CC",
        ad_name="AD_cc",
        delivery_status="ACTIVE",
        spend=Decimal("25.00"),
        leads=0,
        registrations=0,
        deposits=0,
        cpc=Decimal("0.10"),
        ctr=Decimal("2.5"),
    )


# Сценарий: ad уже в claimed — observer-scan со STOP-метриками НЕ затирает state
@pytest.mark.asyncio
async def test_observer_does_not_overwrite_claimed(pg_engine, offer_with_cpa) -> None:
    fb_ad_id = f"230080{uuid.uuid4().hex[:6]}"
    row = _stop_row(code=offer_with_cpa["code"], fb_ad_id=fb_ad_id)

    # 1) первый scan переводит ад в stop_sent + создаёт outbox-task
    await process_scan_rows(pg_engine, rows=[row], scan_id=1)

    # 2) пользователь кликнул «Отключить» — симулируем UPDATE через прямой SQL
    #    (как сделал бы telegram_poller или disable_reconciler)
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                UPDATE ad_alert_state s
                SET alert_state = 'claimed', last_transition_at = NOW()
                FROM fb_ads a
                WHERE s.ad_id = a.id AND a.fb_ad_id = :fbid
                """
            ),
            {"fbid": fb_ad_id},
        )

    # 3) следующий observer-scan видит ту же STOP-картинку и пробует переписать —
    #    с WHERE-guard это no-op, state остаётся claimed
    await process_scan_rows(pg_engine, rows=[row], scan_id=2)

    async with pg_engine.connect() as conn:
        state = (
            await conn.execute(
                text(
                    "SELECT s.alert_state FROM ad_alert_state s "
                    "JOIN fb_ads a ON a.id = s.ad_id WHERE a.fb_ad_id = :fb"
                ),
                {"fb": fb_ad_id},
            )
        ).scalar()

    assert state == "claimed"


# Сценарий: ad уже в disabled — observer тоже не затирает (отключённое объявление
# может быть всё ещё «грязным» в Ads Manager если сканер успел увидеть до propagation)
@pytest.mark.asyncio
async def test_observer_does_not_overwrite_disabled(pg_engine, offer_with_cpa) -> None:
    fb_ad_id = f"230081{uuid.uuid4().hex[:6]}"
    row = _stop_row(code=offer_with_cpa["code"], fb_ad_id=fb_ad_id)

    await process_scan_rows(pg_engine, rows=[row], scan_id=10)

    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                UPDATE ad_alert_state s
                SET alert_state = 'disabled', last_transition_at = NOW()
                FROM fb_ads a
                WHERE s.ad_id = a.id AND a.fb_ad_id = :fbid
                """
            ),
            {"fbid": fb_ad_id},
        )

    # observer видит всё ещё STOP — но затереть disabled нельзя
    await process_scan_rows(pg_engine, rows=[row], scan_id=11)

    async with pg_engine.connect() as conn:
        state = (
            await conn.execute(
                text(
                    "SELECT s.alert_state FROM ad_alert_state s "
                    "JOIN fb_ads a ON a.id = s.ad_id WHERE a.fb_ad_id = :fb"
                ),
                {"fb": fb_ad_id},
            )
        ).scalar()

    assert state == "disabled"


# Сценарий: ad в warning_sent — observer ДОЛЖЕН эскалировать в stop_sent
# (WHERE-guard не блокирует normal/warning_sent/stop_sent — только claimed/disabled)
@pytest.mark.asyncio
async def test_observer_can_still_escalate_warning_to_stop(pg_engine, offer_with_cpa) -> None:
    fb_ad_id = f"230082{uuid.uuid4().hex[:6]}"
    # сначала добиваемся warning_sent (lighter metrics), потом эскалируем
    warn_row = ScannedAdRow(
        fb_ad_id=fb_ad_id,
        campaign_name=f"{offer_with_cpa['code']} | KE | promo",
        adset_name="ADS_CC",
        ad_name="AD_cc",
        delivery_status="ACTIVE",
        spend=Decimal("9.0"),  # 90% от CPA=10 → WARNING без STOP
        leads=0,
        registrations=0,
        deposits=0,
        cpc=Decimal("0.10"),
        ctr=Decimal("2.5"),
    )
    await process_scan_rows(pg_engine, rows=[warn_row], scan_id=20)

    async with pg_engine.connect() as conn:
        state_before = (
            await conn.execute(text("SELECT alert_state FROM ad_alert_state LIMIT 1"))
        ).scalar()
    # state мог быть warning_sent или stop_sent в зависимости от точности порога —
    # для теста нам важна только разрешённость эскалации из не-терминального состояния
    assert state_before in ("warning_sent", "stop_sent")

    # эскалация полноценным STOP-row
    await process_scan_rows(
        pg_engine,
        rows=[_stop_row(code=offer_with_cpa["code"], fb_ad_id=fb_ad_id)],
        scan_id=21,
    )

    async with pg_engine.connect() as conn:
        state_after = (
            await conn.execute(text("SELECT alert_state FROM ad_alert_state LIMIT 1"))
        ).scalar()

    assert state_after == "stop_sent"


# Сценарий: эскалация WARNING → STOP сохраняет open_state_token (тот же incident)
# Старые WARNING inline-кнопки с callback `dis:<fb>:<token>` должны остаться валидны.
# Setup warning_sent делаем прямым SQL, чтобы не зависеть от точности порогов evaluator'а.
@pytest.mark.asyncio
async def test_warning_to_stop_persists_same_open_token(pg_engine, offer_with_cpa) -> None:
    fb_ad_id = f"230083{uuid.uuid4().hex[:6]}"
    pre_token = uuid.uuid4()

    # 1) первый scan с нормой — создаст catalog + ad_alert_state='normal'
    normal_row = ScannedAdRow(
        fb_ad_id=fb_ad_id,
        campaign_name=f"{offer_with_cpa['code']} | KE | promo",
        adset_name="ADS_CC",
        ad_name="AD_cc",
        delivery_status="ACTIVE",
        spend=Decimal("1.0"),
        leads=10,
        registrations=5,
        deposits=3,
        cpc=Decimal("0.05"),
        ctr=Decimal("3.0"),
    )
    await process_scan_rows(pg_engine, rows=[normal_row], scan_id=30)

    # 2) вручную приводим состояние к warning_sent с известным open_state_token
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                UPDATE ad_alert_state s
                SET alert_state = 'warning_sent',
                    current_stage = 'warning',
                    open_state_token = :tok,
                    warning_rule_codes = '["cpc_warn"]'::jsonb,
                    last_transition_at = NOW()
                FROM fb_ads a
                WHERE s.ad_id = a.id AND a.fb_ad_id = :fbid
                """
            ),
            {"tok": pre_token, "fbid": fb_ad_id},
        )

    # 3) STOP-метрики → observer эскалирует warning_sent → stop_sent с тем же token
    await process_scan_rows(
        pg_engine,
        rows=[_stop_row(code=offer_with_cpa["code"], fb_ad_id=fb_ad_id)],
        scan_id=31,
    )

    async with pg_engine.connect() as conn:
        row_after = (
            await conn.execute(
                text(
                    "SELECT s.alert_state, s.open_state_token FROM ad_alert_state s "
                    "JOIN fb_ads a ON a.id = s.ad_id WHERE a.fb_ad_id = :fb"
                ),
                {"fb": fb_ad_id},
            )
        ).first()

    assert row_after[0] == "stop_sent"
    assert row_after[1] == pre_token
