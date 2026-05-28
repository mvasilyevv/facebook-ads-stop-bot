# -*- coding: utf-8 -*-
"""Интеграционные тесты: stop/warning_rule_codes в build_ad_snapshot.

Проверяем, что LATERAL по alert_events корректно разворачивает matched_rule_codes
в поля stop_rule_codes / warning_rule_codes ответа snapshot'а.

Требуется реальный Postgres (docker-compose:5433).
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

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_full_ad(
    conn,
    *,
    suffix: str,
    alert_state: str | None = None,
    alert_event_stage: str | None = None,
    alert_event_codes: list[str] | None = None,
) -> tuple[uuid.UUID, str]:
    """Создаёт цепочку offer→campaign→adset→ad, опционально alert_state + alert_event."""
    offer_id = uuid.uuid4()
    campaign_id = uuid.uuid4()
    adset_id = uuid.uuid4()
    ad_id = uuid.uuid4()
    fb_ad_id = f"RUL{suffix}"

    await conn.execute(
        text("INSERT INTO offers (id, code, name) VALUES (:i, :c, :n)"),
        {"i": offer_id, "c": f"RUL_{suffix}", "n": f"Rule test {suffix}"},
    )
    await conn.execute(
        text("INSERT INTO fb_campaigns (id, campaign_name, offer_id) VALUES (:i, :n, :o)"),
        {"i": campaign_id, "n": f"RUL_CMP_{suffix}", "o": offer_id},
    )
    await conn.execute(
        text("INSERT INTO fb_adsets (id, campaign_id, adset_name) VALUES (:i, :c, :n)"),
        {"i": adset_id, "c": campaign_id, "n": f"RUL_ADS_{suffix}"},
    )
    await conn.execute(
        text(
            "INSERT INTO fb_ads (id, adset_id, fb_ad_id, ad_name, is_active) "
            "VALUES (:i, :a, :f, :n, true)"
        ),
        {"i": ad_id, "a": adset_id, "f": fb_ad_id, "n": f"RUL_AD_{suffix}"},
    )

    if alert_state:
        current_stage = (
            "stop"
            if alert_state == "stop_sent"
            else ("warning" if alert_state == "warning_sent" else None)
        )
        await conn.execute(
            text(
                "INSERT INTO ad_alert_state (ad_id, alert_state, current_stage) "
                "VALUES (:a, :s, :cs)"
            ),
            {"a": ad_id, "s": alert_state, "cs": current_stage},
        )

    if alert_event_stage and alert_event_codes is not None:
        # matched_rule_codes хранится как JSONB.
        # asyncpg quirk: используем CAST вместо ::jsonb (не поддерживается asyncpg).
        # state обязателен: stage → 'warning_sent' / 'stop_sent'.
        state_val = "stop_sent" if alert_event_stage == "stop" else "warning_sent"
        codes_json = json.dumps(alert_event_codes)
        await conn.execute(
            text(
                """
                INSERT INTO alert_events (
                    id, ad_id, stage, state, matched_rule_codes, metrics_json,
                    created_at
                ) VALUES (
                    gen_random_uuid(), :ad, :stage, :state,
                    CAST(:codes AS jsonb), CAST(:mj AS jsonb),
                    NOW() - INTERVAL '10 minutes'
                )
                """
            ),
            {
                "ad": ad_id,
                "stage": alert_event_stage,
                "state": state_val,
                "codes": codes_json,
                "mj": "{}",
            },
        )

    return ad_id, fb_ad_id


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(autouse=True)
async def _clean_rule_code_tables(pg_engine):
    """Чистит тестовые данные до и после каждого теста."""

    async def _clean(conn):
        await conn.execute(
            text("DELETE FROM alert_events WHERE created_at >= NOW() - INTERVAL '1 day'")
        )
        await conn.execute(text("DELETE FROM ad_alert_state"))
        await conn.execute(text("DELETE FROM fb_ads"))
        await conn.execute(text("DELETE FROM fb_adsets"))
        await conn.execute(text("DELETE FROM fb_campaigns"))
        await conn.execute(text("DELETE FROM offers WHERE code LIKE 'RUL_%'"))

    async with pg_engine.begin() as conn:
        await _clean(conn)
    yield
    async with pg_engine.begin() as conn:
        await _clean(conn)


# ---------------------------------------------------------------------------
# Тесты
# ---------------------------------------------------------------------------


# Ad в stop_sent с alert_event matched_rule_codes=['CPL','CPC'] →
# snapshot.stop_rule_codes=['CPL','CPC'], warning_rule_codes=[]
@pytest.mark.asyncio
async def test_stop_ad_returns_stop_rule_codes(pg_engine):
    """Ad в stop_sent + alert_event stage=stop → stop_rule_codes заполнены, warning=[]."""
    async with pg_engine.begin() as conn:
        _, fb_id = await _seed_full_ad(
            conn,
            suffix="S1",
            alert_state="stop_sent",
            alert_event_stage="stop",
            alert_event_codes=["CPL", "CPC"],
        )

    rows = await build_ad_snapshot(pg_engine, fb_ad_ids=[fb_id])
    assert len(rows) == 1
    row = rows[0]
    assert row["stop_rule_codes"] == ["CPL", "CPC"]
    assert row["warning_rule_codes"] == []


# Ad в warning_sent с alert_event matched_rule_codes=['FREQ'] →
# warning_rule_codes=['FREQ'], stop_rule_codes=[]
@pytest.mark.asyncio
async def test_warning_ad_returns_warning_rule_codes(pg_engine):
    """Ad в warning_sent + alert_event stage=warning → warning_rule_codes заполнены, stop=[]."""
    async with pg_engine.begin() as conn:
        _, fb_id = await _seed_full_ad(
            conn,
            suffix="W1",
            alert_state="warning_sent",
            alert_event_stage="warning",
            alert_event_codes=["FREQ"],
        )

    rows = await build_ad_snapshot(pg_engine, fb_ad_ids=[fb_id])
    assert len(rows) == 1
    row = rows[0]
    assert row["warning_rule_codes"] == ["FREQ"]
    assert row["stop_rule_codes"] == []


# Ad без alert_events → оба массива пустые (graceful, без ошибок)
@pytest.mark.asyncio
async def test_ad_without_alert_events_returns_empty_rule_codes(pg_engine):
    """Ad без записей в alert_events → stop/warning_rule_codes=[], не падает."""
    async with pg_engine.begin() as conn:
        _, fb_id = await _seed_full_ad(
            conn,
            suffix="N1",
            alert_state="normal",
            alert_event_stage=None,
            alert_event_codes=None,
        )

    rows = await build_ad_snapshot(pg_engine, fb_ad_ids=[fb_id])
    assert len(rows) == 1
    row = rows[0]
    assert row["stop_rule_codes"] == []
    assert row["warning_rule_codes"] == []


# Partition pruning: alert_events с created_at за пределами lookback → не попадает
@pytest.mark.asyncio
async def test_old_alert_event_outside_lookback_is_excluded(pg_engine):
    """alert_event старше lookback_days не вытаскивается (partition pruning работает)."""
    async with pg_engine.begin() as conn:
        ad_id, fb_id = await _seed_full_ad(
            conn,
            suffix="P1",
            alert_state="stop_sent",
        )
        # Добавляем alert_event за пределами окна (8 дней назад).
        # asyncpg quirk: используем CAST вместо ::jsonb
        codes_json = json.dumps(["CPM"])
        await conn.execute(
            text(
                """
                INSERT INTO alert_events (
                    id, ad_id, stage, state, matched_rule_codes, metrics_json,
                    created_at
                ) VALUES (
                    gen_random_uuid(), :ad, 'stop', 'stop_sent',
                    CAST(:codes AS jsonb), CAST(:mj AS jsonb),
                    NOW() - INTERVAL '8 days'
                )
                """
            ),
            {"ad": ad_id, "codes": codes_json, "mj": "{}"},
        )

    rows = await build_ad_snapshot(pg_engine, fb_ad_ids=[fb_id])
    assert len(rows) == 1
    row = rows[0]
    # Старый event за пределами окна → пустые массивы
    assert row["stop_rule_codes"] == []
    assert row["warning_rule_codes"] == []


# Несколько alert_events → берётся самый свежий (LATERAL LIMIT 1 ORDER BY DESC)
@pytest.mark.asyncio
async def test_latest_alert_event_wins(pg_engine):
    """При нескольких alert_events берётся самый свежий (ORDER BY created_at DESC LIMIT 1)."""
    async with pg_engine.begin() as conn:
        ad_id, fb_id = await _seed_full_ad(
            conn,
            suffix="L1",
            alert_state="stop_sent",
        )
        # Старый ивент warning с одними кодами.
        # asyncpg quirk: используем CAST вместо ::jsonb
        await conn.execute(
            text(
                """
                INSERT INTO alert_events (
                    id, ad_id, stage, state, matched_rule_codes, metrics_json, created_at
                ) VALUES (
                    gen_random_uuid(), :ad, 'warning', 'warning_sent',
                    CAST(:c1 AS jsonb), CAST(:mj AS jsonb),
                    NOW() - INTERVAL '2 hours'
                )
                """
            ),
            {"ad": ad_id, "c1": json.dumps(["OLD_RULE"]), "mj": "{}"},
        )
        # Свежий ивент stop с другими кодами
        await conn.execute(
            text(
                """
                INSERT INTO alert_events (
                    id, ad_id, stage, state, matched_rule_codes, metrics_json, created_at
                ) VALUES (
                    gen_random_uuid(), :ad, 'stop', 'stop_sent',
                    CAST(:c2 AS jsonb), CAST(:mj AS jsonb),
                    NOW() - INTERVAL '1 hour'
                )
                """
            ),
            {"ad": ad_id, "c2": json.dumps(["LATEST_RULE"]), "mj": "{}"},
        )

    rows = await build_ad_snapshot(pg_engine, fb_ad_ids=[fb_id])
    assert len(rows) == 1
    row = rows[0]
    # Побеждает свежий stop-ивент
    assert row["stop_rule_codes"] == ["LATEST_RULE"]
    assert row["warning_rule_codes"] == []


# ---------------------------------------------------------------------------
# Тест через HTTP endpoint /api/dashboard/ads
# ---------------------------------------------------------------------------


# API endpoint /dashboard/ads включает stop_rule_codes в ответ
@pytest.mark.asyncio
async def test_api_dashboard_ads_returns_rule_codes(pg_engine, fake_redis_client):
    """GET /api/dashboard/ads: поля stop/warning_rule_codes присутствуют в ответе."""
    async with pg_engine.begin() as conn:
        _, fb_id = await _seed_full_ad(
            conn,
            suffix="API1",
            alert_state="stop_sent",
            alert_event_stage="stop",
            alert_event_codes=["CPL", "CPC"],
        )

    app = create_app()
    app.dependency_overrides[get_engine] = lambda: pg_engine
    app.dependency_overrides[get_redis] = lambda: fake_redis_client
    app.state.redis = fake_redis_client

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/dashboard/ads", params={"fb_ad_ids": fb_id})

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["stop_rule_codes"] == ["CPL", "CPC"]
    assert data[0]["warning_rule_codes"] == []
