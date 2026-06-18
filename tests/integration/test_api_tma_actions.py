# -*- coding: utf-8 -*-
"""Integration: TMA money-действия (BL-15 Этап 2) + web_app_url (Этап 1).

Money/security: disable отключает реальное объявление через Marketing API (pause_ad).
Поэтому проверяем guard (401 без токена) и фактическое создание нужной записи.

DOM-toggle канал удалён (#fix/remove-dom-toggle). disable ВСЕГДА создаёт
meta_api_mutation pause_ad, ответ channel == 'meta_api'.

Требует Postgres (pg_engine). Cleanup id-scoped (БД общая): fb_ads CASCADE'ит
ad_metrics/ad_alert_state/alert_events, дальше adsets→campaigns→offers вручную
(offer_id = ON DELETE SET NULL, не cascade).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from apps.api.deps import get_engine
from apps.api.main import create_app
from core.auth.tma import issue_session_token
from core.config import get_settings


def _make_app(engine):
    app = create_app()
    app.dependency_overrides[get_engine] = lambda: engine
    return app


def _token_for(uid: int) -> str:
    """Выпускает валидный сессионный токен тем же секретом, что и guard."""
    s = get_settings()
    secret = s.tma_session_secret or s.encryption_key
    return issue_session_token(str(uid), s.tma_session_ttl_seconds, secret)


@pytest_asyncio.fixture
async def tma_factory(pg_engine):
    """Фабрика recipient/ad + token_for с id-scoped teardown."""
    offers: list[uuid.UUID] = []
    campaigns: list[uuid.UUID] = []
    adsets: list[uuid.UUID] = []
    ads: list[uuid.UUID] = []
    recipients: list[tuple[int, int]] = []

    async def make_recipient(
        uid: int, *, chat_id: int | None = None, role: str = "recipient"
    ) -> int:
        cid = chat_id if chat_id is not None else uid
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO telegram_recipients (chat_id, telegram_user_id, username, role)
                    VALUES (:c, :u, :un, :r)
                    ON CONFLICT (chat_id, telegram_user_id)
                    DO UPDATE SET role = EXCLUDED.role, revoked_at = NULL
                    """
                ),
                {"c": cid, "u": uid, "un": f"user{uid}", "r": role},
            )
        recipients.append((cid, uid))
        return cid

    async def make_ad(
        fb_ad_id: str,
        *,
        alert_state: str | None = None,
        open_token: uuid.UUID | None = None,
        snoozed_until: datetime | None = None,
        metrics: dict | None = None,
        ad_name: str = "AD",
        campaign_name: str = "CMP",
    ) -> uuid.UUID:
        offer_id, campaign_id, adset_id, ad_id = (uuid.uuid4() for _ in range(4))
        suffix = uuid.uuid4().hex[:8]
        async with pg_engine.begin() as conn:
            await conn.execute(
                text("INSERT INTO offers (id, code, name) VALUES (:i, :c, :n)"),
                {"i": offer_id, "c": f"TMA_{suffix}", "n": f"offer {suffix}"},
            )
            await conn.execute(
                text("INSERT INTO fb_campaigns (id, campaign_name, offer_id) VALUES (:i, :n, :o)"),
                {"i": campaign_id, "n": campaign_name, "o": offer_id},
            )
            await conn.execute(
                text("INSERT INTO fb_adsets (id, campaign_id, adset_name) VALUES (:i, :c, :n)"),
                {"i": adset_id, "c": campaign_id, "n": f"ADSET_{suffix}"},
            )
            await conn.execute(
                text(
                    "INSERT INTO fb_ads (id, adset_id, fb_ad_id, ad_name) VALUES (:i, :a, :f, :n)"
                ),
                {"i": ad_id, "a": adset_id, "f": fb_ad_id, "n": ad_name},
            )
            if alert_state is not None or snoozed_until is not None:
                await conn.execute(
                    text(
                        """
                        INSERT INTO ad_alert_state
                            (ad_id, alert_state, open_state_token, snoozed_until)
                        VALUES (:ad, :st, :tok, :sn)
                        """
                    ),
                    {
                        "ad": ad_id,
                        "st": alert_state or "normal",
                        "tok": open_token,
                        "sn": snoozed_until,
                    },
                )
            if metrics:
                await conn.execute(
                    text(
                        """
                        INSERT INTO ad_metrics
                            (id, ad_id, cycle_ts, spend, clicks, cpc, ctr,
                             leads, registrations, deposits, cost_per_lead)
                        VALUES (:id, :ad, :ts, :spend, :clicks, :cpc, :ctr,
                                :leads, :regs, :deps, :cpl)
                        """
                    ),
                    {
                        "id": uuid.uuid4(),
                        "ad": ad_id,
                        "ts": datetime.now(UTC),
                        "spend": metrics.get("spend"),
                        "clicks": metrics.get("clicks"),
                        "cpc": metrics.get("cpc"),
                        "ctr": metrics.get("ctr"),
                        "leads": metrics.get("leads"),
                        "regs": metrics.get("registrations"),
                        "deps": metrics.get("deposits"),
                        "cpl": metrics.get("cost_per_lead"),
                    },
                )
        offers.append(offer_id)
        campaigns.append(campaign_id)
        adsets.append(adset_id)
        ads.append(ad_id)
        return ad_id

    yield SimpleNamespace(
        make_recipient=make_recipient,
        make_ad=make_ad,
        token_for=_token_for,
    )

    async with pg_engine.begin() as conn:
        if ads:
            await conn.execute(text("DELETE FROM fb_ads WHERE id = ANY(:ids)"), {"ids": ads})
        if adsets:
            await conn.execute(text("DELETE FROM fb_adsets WHERE id = ANY(:ids)"), {"ids": adsets})
        if campaigns:
            await conn.execute(
                text("DELETE FROM fb_campaigns WHERE id = ANY(:ids)"), {"ids": campaigns}
            )
        if offers:
            await conn.execute(text("DELETE FROM offers WHERE id = ANY(:ids)"), {"ids": offers})
        for cid, uid in recipients:
            await conn.execute(
                text(
                    "DELETE FROM telegram_recipients WHERE chat_id = :c AND telegram_user_id = :u"
                ),
                {"c": cid, "u": uid},
            )


# ─────────────────────────── GUARD: 401 без токена ───────────────────────────


# Все TMA-action-endpoint'ы без Bearer → 401 (money-действия закрыты)
@pytest.mark.asyncio
async def test_actions_require_token_401(pg_engine, tma_factory):
    app = _make_app(pg_engine)
    fb = f"tma401_{uuid.uuid4().hex[:8]}"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        r1 = await ac.get(f"/api/tma/ads/{fb}")
        r2 = await ac.post(f"/api/tma/ads/{fb}/disable", json={"reason": "x"})
        r3 = await ac.post(f"/api/tma/ads/{fb}/snooze", json={"minutes": 30})
        r4 = await ac.post(f"/api/tma/ads/{fb}/claim", json={})
    assert [r.status_code for r in (r1, r2, r3, r4)] == [401] * 4


# ─────────────────────────── GET /tma/ads/{id} ───────────────────────────────


# Детальный снимок: state → UPPERCASE, метрики из ad.metrics.*, recent_alerts
@pytest.mark.asyncio
async def test_ad_detail_shape(pg_engine, tma_factory):
    uid = 7200001
    await tma_factory.make_recipient(uid, role="recipient")
    fb = f"tmaDetail_{uuid.uuid4().hex[:8]}"
    await tma_factory.make_ad(
        fb,
        alert_state="stop_sent",
        ad_name="Объявление X",
        metrics={"spend": "12.50", "clicks": 100, "cpc": "0.1250", "leads": 4, "deposits": 1},
    )
    app = _make_app(pg_engine)
    headers = {"Authorization": f"Bearer {tma_factory.token_for(uid)}"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        resp = await ac.get(f"/api/tma/ads/{fb}", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["fb_ad_id"] == fb
    assert body["state"] == "STOP_SENT"  # lowercase БД → UPPERCASE фронт
    assert body["ad_name"] == "Объявление X"
    assert body["metrics"]["spend"] == "12.50"
    assert body["metrics"]["leads"] == 4
    assert body["can_open_in_ads_manager"] is False  # нет meta_api_observation


# Несуществующее объявление → 404
@pytest.mark.asyncio
async def test_ad_detail_404(pg_engine, tma_factory):
    uid = 7200002
    await tma_factory.make_recipient(uid)
    app = _make_app(pg_engine)
    headers = {"Authorization": f"Bearer {tma_factory.token_for(uid)}"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        resp = await ac.get(f"/api/tma/ads/nope_{uuid.uuid4().hex}", headers=headers)
    assert resp.status_code == 404


# ─────────────────────────── POST disable ────────────────────────────────────


# disable всегда создаёт meta_api_mutation pause_ad, channel == 'meta_api'
@pytest.mark.asyncio
async def test_disable_creates_meta_api_mutation(pg_engine, tma_factory):
    uid = 7200003
    await tma_factory.make_recipient(uid)
    fb = f"tmaDisApi_{uuid.uuid4().hex[:8]}"
    await tma_factory.make_ad(fb, alert_state="stop_sent")

    app = _make_app(pg_engine)
    headers = {"Authorization": f"Bearer {tma_factory.token_for(uid)}"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        resp = await ac.post(
            f"/api/tma/ads/{fb}/disable", json={"reason": "дорого"}, headers=headers
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["channel"] == "meta_api"
    assert body.get("task_id") is not None  # id задачи всегда присвоен

    # В task_queue появилась meta_api_mutation pause_ad с target_id == fb_ad_id
    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT task_type, status, requested_by,
                           payload->>'mutation_kind' AS kind,
                           payload->>'target_id' AS target
                    FROM task_queue
                    WHERE task_type = 'meta_api_mutation' AND payload->>'target_id' = :fb
                    ORDER BY created_at DESC LIMIT 1
                    """
                ),
                {"fb": fb},
            )
        ).first()
    assert row is not None
    assert row.kind == "pause_ad"
    assert row.status == "pending"
    assert row.requested_by == f"tma:{uid}"
    # cleanup задачи
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                "DELETE FROM task_queue"
                " WHERE task_type='meta_api_mutation' AND payload->>'target_id'=:fb"
            ),
            {"fb": fb},
        )


# повторный disable с тем же token идемпотентен — задача не дублируется
@pytest.mark.asyncio
async def test_disable_idempotent_by_token(pg_engine, tma_factory):
    uid = 7200004
    await tma_factory.make_recipient(uid)
    fb = f"tmaDisIdem_{uuid.uuid4().hex[:8]}"
    await tma_factory.make_ad(fb, alert_state="stop_sent")
    token = str(uuid.uuid4())

    app = _make_app(pg_engine)
    headers = {"Authorization": f"Bearer {tma_factory.token_for(uid)}"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        r1 = await ac.post(f"/api/tma/ads/{fb}/disable", json={"token": token}, headers=headers)
        r2 = await ac.post(f"/api/tma/ads/{fb}/disable", json={"token": token}, headers=headers)
    assert r1.status_code == 200, r1.text
    assert r2.status_code == 200, r2.text
    # оба запроса вернули одинаковый task_id
    assert r1.json()["task_id"] == r2.json()["task_id"]

    # В task_queue ровно одна задача с этим target_id
    async with pg_engine.connect() as conn:
        count = (
            await conn.execute(
                text(
                    "SELECT COUNT(*) FROM task_queue"
                    " WHERE task_type='meta_api_mutation' AND payload->>'target_id'=:fb"
                ),
                {"fb": fb},
            )
        ).scalar()
    assert count == 1
    # cleanup задачи
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                "DELETE FROM task_queue"
                " WHERE task_type='meta_api_mutation' AND payload->>'target_id'=:fb"
            ),
            {"fb": fb},
        )


# disable несуществующего объявления → 404 (задачу не создаём)
@pytest.mark.asyncio
async def test_disable_404(pg_engine, tma_factory):
    uid = 7200005
    await tma_factory.make_recipient(uid)
    app = _make_app(pg_engine)
    headers = {"Authorization": f"Bearer {tma_factory.token_for(uid)}"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        resp = await ac.post(
            f"/api/tma/ads/missing_{uuid.uuid4().hex}/disable", json={}, headers=headers
        )
    assert resp.status_code == 404


# ─────────────────────────── POST snooze ─────────────────────────────────────


# snooze ставит ad_alert_state.snoozed_until в будущее
@pytest.mark.asyncio
async def test_snooze_sets_until(pg_engine, tma_factory):
    uid = 7200006
    await tma_factory.make_recipient(uid)
    fb = f"tmaSnz_{uuid.uuid4().hex[:8]}"
    ad_id = await tma_factory.make_ad(fb, alert_state="warning_sent")
    app = _make_app(pg_engine)
    headers = {"Authorization": f"Bearer {tma_factory.token_for(uid)}"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        resp = await ac.post(f"/api/tma/ads/{fb}/snooze", json={"minutes": 60}, headers=headers)
    assert resp.status_code == 200, resp.text
    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT snoozed_until FROM ad_alert_state WHERE ad_id = :ad"),
                {"ad": ad_id},
            )
        ).first()
    assert row.snoozed_until is not None
    assert row.snoozed_until > datetime.now(UTC)


# snooze объявления без ad_alert_state → 409 (нечего снузить)
@pytest.mark.asyncio
async def test_snooze_no_state_409(pg_engine, tma_factory):
    uid = 7200007
    await tma_factory.make_recipient(uid)
    fb = f"tmaSnzNo_{uuid.uuid4().hex[:8]}"
    await tma_factory.make_ad(fb)  # без alert_state
    app = _make_app(pg_engine)
    headers = {"Authorization": f"Bearer {tma_factory.token_for(uid)}"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        resp = await ac.post(f"/api/tma/ads/{fb}/snooze", json={"minutes": 30}, headers=headers)
    assert resp.status_code == 409


# snooze с minutes вне диапазона → 422 (валидация Pydantic)
@pytest.mark.asyncio
async def test_snooze_bad_minutes_422(pg_engine, tma_factory):
    uid = 7200008
    await tma_factory.make_recipient(uid)
    fb = f"tmaSnzBad_{uuid.uuid4().hex[:8]}"
    await tma_factory.make_ad(fb, alert_state="warning_sent")
    app = _make_app(pg_engine)
    headers = {"Authorization": f"Bearer {tma_factory.token_for(uid)}"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        resp = await ac.post(f"/api/tma/ads/{fb}/snooze", json={"minutes": 99999}, headers=headers)
    assert resp.status_code == 422


# ─────────────────────────── POST claim ──────────────────────────────────────


# claim переводит stop_sent → claimed
@pytest.mark.asyncio
async def test_claim_sets_claimed(pg_engine, tma_factory):
    uid = 7200009
    await tma_factory.make_recipient(uid)
    fb = f"tmaClaim_{uuid.uuid4().hex[:8]}"
    ad_id = await tma_factory.make_ad(fb, alert_state="stop_sent")
    app = _make_app(pg_engine)
    headers = {"Authorization": f"Bearer {tma_factory.token_for(uid)}"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        resp = await ac.post(f"/api/tma/ads/{fb}/claim", json={}, headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["alert_state"] == "claimed"
    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT alert_state FROM ad_alert_state WHERE ad_id = :ad"), {"ad": ad_id}
            )
        ).first()
    assert row.alert_state == "claimed"


# claim из normal (нет активного алерта) → 409, состояние не меняется
@pytest.mark.asyncio
async def test_claim_normal_409(pg_engine, tma_factory):
    uid = 7200010
    await tma_factory.make_recipient(uid)
    fb = f"tmaClaimN_{uuid.uuid4().hex[:8]}"
    ad_id = await tma_factory.make_ad(fb, alert_state="normal")
    app = _make_app(pg_engine)
    headers = {"Authorization": f"Bearer {tma_factory.token_for(uid)}"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        resp = await ac.post(f"/api/tma/ads/{fb}/claim", json={}, headers=headers)
    assert resp.status_code == 409
    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT alert_state FROM ad_alert_state WHERE ad_id = :ad"), {"ad": ad_id}
            )
        ).first()
    assert row.alert_state == "normal"


# claim уже-claimed → идемпотентно ok
@pytest.mark.asyncio
async def test_claim_idempotent(pg_engine, tma_factory):
    uid = 7200011
    await tma_factory.make_recipient(uid)
    fb = f"tmaClaimI_{uuid.uuid4().hex[:8]}"
    await tma_factory.make_ad(fb, alert_state="claimed")
    app = _make_app(pg_engine)
    headers = {"Authorization": f"Bearer {tma_factory.token_for(uid)}"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        resp = await ac.post(f"/api/tma/ads/{fb}/claim", json={}, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["alert_state"] == "claimed"
