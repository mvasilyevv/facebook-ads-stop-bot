# -*- coding: utf-8 -*-
"""Тесты desktop snooze: POST /api/dashboard/ads/{id}/snooze + bulk-snooze.

Покрытие: happy (snoozed_until выставлен), 404 (нет ad), 409 (нет состояния),
граничные minutes (валидация ge/le → 422), bulk partial-failure (snoozed/failed
с разбивкой no_ad / no_alert_state), bulk cap → 422.

Требует Postgres. Cleanup id-scoped по префиксу 99SNZ.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from apps.api.deps import get_engine, get_redis
from apps.api.main import create_app


def _make_app(engine, redis=None):
    app = create_app()
    app.dependency_overrides[get_engine] = lambda: engine
    if redis is not None:
        app.dependency_overrides[get_redis] = lambda: redis
        app.state.redis = redis
    return app


@pytest_asyncio.fixture
async def clean_snz(pg_engine):
    """Чистит тестовые объявления snooze-тестов (CASCADE на ad_alert_state)."""

    async def _cleanup():
        async with pg_engine.begin() as conn:
            await conn.execute(text("DELETE FROM fb_ads WHERE fb_ad_id LIKE '99SNZ%'"))
            await conn.execute(text("DELETE FROM fb_adsets WHERE adset_name LIKE 'SNZ_%'"))
            await conn.execute(text("DELETE FROM fb_campaigns WHERE campaign_name LIKE 'SNZ_%'"))
            await conn.execute(text("DELETE FROM offers WHERE code LIKE 'SNZ_%'"))

    await _cleanup()
    yield
    await _cleanup()


async def _seed_ad(conn, suffix: str, *, with_state: bool) -> str:
    """offer→campaign→adset→ad (+опц. ad_alert_state). Возвращает fb_ad_id."""
    offer_id, campaign_id, adset_id, ad_id = (uuid.uuid4() for _ in range(4))
    fb_ad_id = f"99SNZ{suffix}"
    await conn.execute(
        text("INSERT INTO offers (id, code, name) VALUES (:i, :c, :n)"),
        {"i": offer_id, "c": f"SNZ_{suffix}", "n": f"Snz offer {suffix}"},
    )
    await conn.execute(
        text("INSERT INTO fb_campaigns (id, campaign_name, offer_id) VALUES (:i, :n, :o)"),
        {"i": campaign_id, "n": f"SNZ_{suffix}", "o": offer_id},
    )
    await conn.execute(
        text("INSERT INTO fb_adsets (id, campaign_id, adset_name) VALUES (:i, :c, :n)"),
        {"i": adset_id, "c": campaign_id, "n": f"SNZ_{suffix}"},
    )
    await conn.execute(
        text("INSERT INTO fb_ads (id, adset_id, fb_ad_id, ad_name) VALUES (:i, :a, :f, :n)"),
        {"i": ad_id, "a": adset_id, "f": fb_ad_id, "n": f"Snz AD {suffix}"},
    )
    if with_state:
        await conn.execute(
            text("INSERT INTO ad_alert_state (ad_id, alert_state) VALUES (:ad, 'warning_sent')"),
            {"ad": ad_id},
        )
    return fb_ad_id


async def _read_snooze(engine, fb_ad_id: str):
    """Читает snoozed_until для ad. None если строки состояния нет."""
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT s.snoozed_until FROM ad_alert_state s "
                    "JOIN fb_ads a ON a.id = s.ad_id WHERE a.fb_ad_id = :fid"
                ),
                {"fid": fb_ad_id},
            )
        ).first()
    return row.snoozed_until if row else None


# ─── Тест 1: happy — snooze выставляет snoozed_until в будущее ─────────────────
@pytest.mark.asyncio
async def test_snooze_happy(pg_engine, fake_redis_client, clean_snz) -> None:
    """POST snooze для ad с состоянием → 200, snoozed_until в будущем."""
    sfx = uuid.uuid4().hex[:6]
    async with pg_engine.begin() as conn:
        fid = await _seed_ad(conn, sfx, with_state=True)

    app = _make_app(pg_engine, fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(f"/api/dashboard/ads/{fid}/snooze", json={"minutes": 60})

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["fb_ad_id"] == fid
    snoozed = await _read_snooze(pg_engine, fid)
    assert snoozed is not None and snoozed > datetime.now(timezone.utc)


# ─── Тест 2: 404 — несуществующего объявления нельзя снузить ───────────────────
@pytest.mark.asyncio
async def test_snooze_unknown_ad(pg_engine, fake_redis_client, clean_snz) -> None:
    """POST snooze для несуществующего fb_ad_id → 404."""
    app = _make_app(pg_engine, fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/api/dashboard/ads/99SNZdoesnotexist/snooze", json={"minutes": 30})
    assert resp.status_code == 404


# ─── Тест 3: 409 — у объявления нет строки состояния, нечего снузить ───────────
@pytest.mark.asyncio
async def test_snooze_no_alert_state(pg_engine, fake_redis_client, clean_snz) -> None:
    """POST snooze для ad без ad_alert_state → 409."""
    sfx = uuid.uuid4().hex[:6]
    async with pg_engine.begin() as conn:
        fid = await _seed_ad(conn, sfx, with_state=False)

    app = _make_app(pg_engine, fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(f"/api/dashboard/ads/{fid}/snooze", json={"minutes": 30})
    assert resp.status_code == 409


# ─── Тест 4: граничные minutes — 0 и >1440 отклоняются валидацией ──────────────
@pytest.mark.asyncio
async def test_snooze_minutes_bounds(pg_engine, fake_redis_client, clean_snz) -> None:
    """minutes=0 → 422 (ge=1); minutes=1441 → 422 (le=1440)."""
    sfx = uuid.uuid4().hex[:6]
    async with pg_engine.begin() as conn:
        fid = await _seed_ad(conn, sfx, with_state=True)

    app = _make_app(pg_engine, fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        zero = await ac.post(f"/api/dashboard/ads/{fid}/snooze", json={"minutes": 0})
        too_big = await ac.post(f"/api/dashboard/ads/{fid}/snooze", json={"minutes": 1441})
        # Верхняя граница включительно — допустима.
        edge = await ac.post(f"/api/dashboard/ads/{fid}/snooze", json={"minutes": 1440})

    assert zero.status_code == 422
    assert too_big.status_code == 422
    assert edge.status_code == 200


# ─── Тест 5: bulk happy — все валидные снузятся одним until ────────────────────
@pytest.mark.asyncio
async def test_bulk_snooze_happy(pg_engine, fake_redis_client, clean_snz) -> None:
    """bulk-snooze 3 валидных ad → snoozed=3, failed=[], общий snoozed_until."""
    sfx = uuid.uuid4().hex[:6]
    async with pg_engine.begin() as conn:
        ids = [await _seed_ad(conn, f"{sfx}{i}", with_state=True) for i in range(3)]

    app = _make_app(pg_engine, fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/dashboard/ads/bulk-snooze",
            json={"fb_ad_ids": ids, "minutes": 120},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert set(data["snoozed"]) == set(ids)
    assert data["failed"] == []
    for fid in ids:
        snoozed = await _read_snooze(pg_engine, fid)
        assert snoozed is not None and snoozed > datetime.now(timezone.utc)


# ─── Тест 6: bulk partial-failure — смесь валидных/без состояния/несуществующих ─
@pytest.mark.asyncio
async def test_bulk_snooze_partial_failure(pg_engine, fake_redis_client, clean_snz) -> None:
    """bulk: валидный → snoozed; без состояния → failed(no_alert_state); нет ad → failed(no_ad)."""
    sfx = uuid.uuid4().hex[:6]
    async with pg_engine.begin() as conn:
        ok = await _seed_ad(conn, f"{sfx}OK", with_state=True)
        no_state = await _seed_ad(conn, f"{sfx}NS", with_state=False)
    missing = f"99SNZ{sfx}MISS"

    app = _make_app(pg_engine, fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/dashboard/ads/bulk-snooze",
            json={"fb_ad_ids": [ok, no_state, missing], "minutes": 45},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["snoozed"] == [ok]
    reasons = {f["fb_ad_id"]: f["reason"] for f in data["failed"]}
    assert reasons == {no_state: "no_alert_state", missing: "no_ad"}


# ─── Тест 7: bulk cap — превышение лимита → 422 ────────────────────────────────
@pytest.mark.asyncio
async def test_bulk_snooze_cap_exceeded(pg_engine, fake_redis_client, clean_snz) -> None:
    """bulk-snooze >50 ad → 422."""
    ids = [f"99SNZ{uuid.uuid4().hex[:8]}" for _ in range(51)]
    app = _make_app(pg_engine, fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/dashboard/ads/bulk-snooze",
            json={"fb_ad_ids": ids, "minutes": 30},
        )
    assert resp.status_code == 422


# ─── Тест 8: bulk empty — пустой список → 422 ──────────────────────────────────
@pytest.mark.asyncio
async def test_bulk_snooze_empty(pg_engine, fake_redis_client, clean_snz) -> None:
    """Пустой fb_ad_ids → 422 (min_length=1)."""
    app = _make_app(pg_engine, fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/dashboard/ads/bulk-snooze",
            json={"fb_ad_ids": [], "minutes": 30},
        )
    assert resp.status_code == 422
