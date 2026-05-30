# -*- coding: utf-8 -*-
"""Интеграционные тесты BL-12-mig: закрытие shape-расхождений frontend↔backend.

Покрывает три изменения:
  1. delivery_status персистится в fb_ads через upsert_catalog_hierarchy и
     читается build_ad_snapshot (раньше — захардкоженный null).
  2. last_warning_at / last_stop_at — реальные времена из alert_events, а не
     лоссовая реконструкция из current_stage (ad warning→stop теперь несёт ОБА
     времени, раньше last_warning_at терялся).
  3. triggered_by_rule_codes в /api/dashboard/alerts = matched_rule_codes
     (раньше всегда null).

Требуется реальный Postgres (docker-compose:5433). Cleanup prefix-scoped по
'BL12%' — БД общая с другими сессиями, глобальный DELETE запрещён.
"""

from __future__ import annotations

import json
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from apps.api.deps import get_engine, get_redis
from apps.api.main import create_app
from core.dashboard.snapshot import build_ad_snapshot
from core.observer.writers import upsert_catalog_hierarchy

_PREFIX = "BL12"


# ---------------------------------------------------------------------------
# Helpers / Fixtures
# ---------------------------------------------------------------------------


async def _seed_ad_with_events(
    conn,
    *,
    suffix: str,
    warning_minutes_ago: int | None = None,
    stop_minutes_ago: int | None = None,
) -> tuple[uuid.UUID, str]:
    """Создаёт offer→campaign→adset→ad (stop_sent) + опциональные warning/stop events."""
    offer_id = uuid.uuid4()
    campaign_id = uuid.uuid4()
    adset_id = uuid.uuid4()
    ad_id = uuid.uuid4()
    fb_ad_id = f"{_PREFIX}{suffix}"

    await conn.execute(
        text("INSERT INTO offers (id, code, name) VALUES (:i, :c, :n)"),
        {"i": offer_id, "c": f"{_PREFIX}_{suffix}", "n": f"BL12 {suffix}"},
    )
    await conn.execute(
        text("INSERT INTO fb_campaigns (id, campaign_name, offer_id) VALUES (:i, :n, :o)"),
        {"i": campaign_id, "n": f"{_PREFIX}_CMP_{suffix}", "o": offer_id},
    )
    await conn.execute(
        text("INSERT INTO fb_adsets (id, campaign_id, adset_name) VALUES (:i, :c, :n)"),
        {"i": adset_id, "c": campaign_id, "n": f"{_PREFIX}_ADS_{suffix}"},
    )
    await conn.execute(
        text(
            "INSERT INTO fb_ads (id, adset_id, fb_ad_id, ad_name, is_active) "
            "VALUES (:i, :a, :f, :n, true)"
        ),
        {"i": ad_id, "a": adset_id, "f": fb_ad_id, "n": f"{_PREFIX}_AD_{suffix}"},
    )
    # ad_alert_state: stop_sent / current_stage=stop — как у эскалированного ad'а.
    await conn.execute(
        text(
            "INSERT INTO ad_alert_state (ad_id, alert_state, current_stage) "
            "VALUES (:a, 'stop_sent', 'stop')"
        ),
        {"a": ad_id},
    )

    async def _insert_event(stage: str, minutes_ago: int) -> None:
        state_val = "stop_sent" if stage == "stop" else "warning_sent"
        await conn.execute(
            text(
                """
                INSERT INTO alert_events (
                    id, ad_id, stage, state, matched_rule_codes, metrics_json, created_at
                ) VALUES (
                    gen_random_uuid(), :ad, :stage, :state,
                    CAST(:codes AS jsonb), CAST(:mj AS jsonb),
                    NOW() - make_interval(mins => :mins)
                )
                """
            ),
            {
                "ad": ad_id,
                "stage": stage,
                "state": state_val,
                "codes": json.dumps(["CPL"]),
                "mj": "{}",
                "mins": minutes_ago,
            },
        )

    if warning_minutes_ago is not None:
        await _insert_event("warning", warning_minutes_ago)
    if stop_minutes_ago is not None:
        await _insert_event("stop", stop_minutes_ago)

    return ad_id, fb_ad_id


@pytest_asyncio.fixture(autouse=True)
async def _clean_bl12(pg_engine):
    """Prefix-scoped cleanup по 'BL12%' до и после теста (БД общая)."""

    async def _clean(conn):
        ids = (
            (
                await conn.execute(
                    text("SELECT id FROM fb_ads WHERE fb_ad_id LIKE :p"),
                    {"p": f"{_PREFIX}%"},
                )
            )
            .scalars()
            .all()
        )
        if ids:
            await conn.execute(
                text("DELETE FROM alert_events WHERE ad_id = ANY(:ids)"), {"ids": list(ids)}
            )
            await conn.execute(
                text("DELETE FROM ad_metrics WHERE ad_id = ANY(:ids)"), {"ids": list(ids)}
            )
            await conn.execute(
                text("DELETE FROM ad_alert_state WHERE ad_id = ANY(:ids)"), {"ids": list(ids)}
            )
        await conn.execute(text("DELETE FROM fb_ads WHERE fb_ad_id LIKE :p"), {"p": f"{_PREFIX}%"})
        await conn.execute(
            text("DELETE FROM fb_adsets WHERE adset_name LIKE :p"), {"p": f"{_PREFIX}%"}
        )
        await conn.execute(
            text("DELETE FROM fb_campaigns WHERE campaign_name LIKE :p"), {"p": f"{_PREFIX}%"}
        )
        await conn.execute(text("DELETE FROM offers WHERE code LIKE :p"), {"p": f"{_PREFIX}%"})

    async with pg_engine.begin() as conn:
        await _clean(conn)
    yield
    async with pg_engine.begin() as conn:
        await _clean(conn)


# ---------------------------------------------------------------------------
# 1. delivery_status persist
# ---------------------------------------------------------------------------


# upsert_catalog_hierarchy пишет delivery_status в fb_ads, snapshot его читает.
@pytest.mark.asyncio
async def test_delivery_status_persisted_and_returned(pg_engine):
    """delivery_status='Active' → попадает в fb_ads и в build_ad_snapshot."""
    fb_ad_id = f"{_PREFIX}DS1"
    async with pg_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO offers (id, code, name) VALUES (:i, :c, :n)"),
            {"i": uuid.uuid4(), "c": f"{_PREFIX}_DS1", "n": "BL12 DS1"},
        )
    await upsert_catalog_hierarchy(
        pg_engine,
        fb_ad_id=fb_ad_id,
        ad_name=f"{_PREFIX}_AD_DS1",
        fb_adset_id=None,
        adset_name=f"{_PREFIX}_ADS_DS1",
        fb_campaign_id=None,
        campaign_name=f"{_PREFIX}_CMP_DS1",
        offer_id=None,
        delivery_status="Active",
    )

    rows = await build_ad_snapshot(pg_engine, fb_ad_ids=[fb_ad_id])
    assert len(rows) == 1
    assert rows[0]["delivery_status"] == "Active"


# Повторный upsert с другим статусом обновляет значение (ON CONFLICT DO UPDATE).
@pytest.mark.asyncio
async def test_delivery_status_updated_on_rescan(pg_engine):
    """Второй скан с 'Disapproved' перезаписывает прежний 'Active'."""
    fb_ad_id = f"{_PREFIX}DS2"
    common = dict(
        fb_ad_id=fb_ad_id,
        ad_name=f"{_PREFIX}_AD_DS2",
        fb_adset_id=None,
        adset_name=f"{_PREFIX}_ADS_DS2",
        fb_campaign_id=None,
        campaign_name=f"{_PREFIX}_CMP_DS2",
        offer_id=None,
    )
    await upsert_catalog_hierarchy(pg_engine, **common, delivery_status="Active")
    await upsert_catalog_hierarchy(pg_engine, **common, delivery_status="Disapproved")

    rows = await build_ad_snapshot(pg_engine, fb_ad_ids=[fb_ad_id])
    assert rows[0]["delivery_status"] == "Disapproved"


# Пустая строка delivery_status нормализуется в NULL (не "" во фронте).
@pytest.mark.asyncio
async def test_delivery_status_empty_string_becomes_null(pg_engine):
    """delivery_status='' → NULL в fb_ads (фронту не нужен пустой статус)."""
    fb_ad_id = f"{_PREFIX}DS3"
    await upsert_catalog_hierarchy(
        pg_engine,
        fb_ad_id=fb_ad_id,
        ad_name=f"{_PREFIX}_AD_DS3",
        fb_adset_id=None,
        adset_name=f"{_PREFIX}_ADS_DS3",
        fb_campaign_id=None,
        campaign_name=f"{_PREFIX}_CMP_DS3",
        offer_id=None,
        delivery_status="   ",
    )
    rows = await build_ad_snapshot(pg_engine, fb_ad_ids=[fb_ad_id])
    assert rows[0]["delivery_status"] is None


# ---------------------------------------------------------------------------
# 2. last_warning_at / last_stop_at из alert_events
# ---------------------------------------------------------------------------


# Ключевой кейс: ad warning→stop несёт ОБА времени (раньше last_warning_at терялся).
@pytest.mark.asyncio
async def test_last_warning_and_stop_both_present_after_escalation(pg_engine):
    """Ad в stop_sent с warning(-2ч) и stop(-1ч) events → оба времени заполнены."""
    async with pg_engine.begin() as conn:
        _, fb_id = await _seed_ad_with_events(
            conn, suffix="LW1", warning_minutes_ago=120, stop_minutes_ago=60
        )

    rows = await build_ad_snapshot(pg_engine, fb_ad_ids=[fb_id])
    assert len(rows) == 1
    row = rows[0]
    # Старый CASE WHEN current_stage='stop' давал last_warning_at=None — регресс закрыт.
    assert row["last_warning_at"] is not None
    assert row["last_stop_at"] is not None
    # stop позже warning (был -1ч против -2ч).
    assert row["last_stop_at"] > row["last_warning_at"]


# Ad только с warning event → last_warning_at заполнен, last_stop_at = None.
@pytest.mark.asyncio
async def test_last_warning_only(pg_engine):
    """Только warning event → last_warning_at не None, last_stop_at None."""
    async with pg_engine.begin() as conn:
        _, fb_id = await _seed_ad_with_events(
            conn, suffix="LW2", warning_minutes_ago=30, stop_minutes_ago=None
        )

    rows = await build_ad_snapshot(pg_engine, fb_ad_ids=[fb_id])
    row = rows[0]
    assert row["last_warning_at"] is not None
    assert row["last_stop_at"] is None


# Событие старше lookback (8 дней) не учитывается (partition pruning / окно).
@pytest.mark.asyncio
async def test_event_outside_lookback_not_counted(pg_engine):
    """warning event 8 дней назад → last_warning_at=None (за окном lookback)."""
    async with pg_engine.begin() as conn:
        _, fb_id = await _seed_ad_with_events(
            conn, suffix="LW3", warning_minutes_ago=8 * 24 * 60, stop_minutes_ago=None
        )

    rows = await build_ad_snapshot(pg_engine, fb_ad_ids=[fb_id])
    row = rows[0]
    assert row["last_warning_at"] is None


# ---------------------------------------------------------------------------
# 3. triggered_by_rule_codes через HTTP /api/dashboard/alerts
# ---------------------------------------------------------------------------


# /api/dashboard/alerts отдаёт triggered_by_rule_codes = matched_rule_codes (не null).
@pytest.mark.asyncio
async def test_api_alerts_triggered_by_aliases_matched(pg_engine, fake_redis_client):
    """GET /api/dashboard/alerts: triggered_by_rule_codes дублирует matched_rule_codes."""
    async with pg_engine.begin() as conn:
        await _seed_ad_with_events(
            conn, suffix="TR1", warning_minutes_ago=None, stop_minutes_ago=30
        )

    app = create_app()
    app.dependency_overrides[get_engine] = lambda: pg_engine
    app.dependency_overrides[get_redis] = lambda: fake_redis_client
    app.state.redis = fake_redis_client

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/dashboard/alerts", params={"fb_ad_id": f"{_PREFIX}TR1"})

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1
    ev = data[0]
    assert ev["matched_rule_codes"] == ["CPL"]
    assert ev["triggered_by_rule_codes"] == ["CPL"]
