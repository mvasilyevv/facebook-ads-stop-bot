# -*- coding: utf-8 -*-
"""Интеграционные тесты для GET/PUT/DELETE /api/settings/telegram и recipients/invite.

Требует живой Postgres (docker-compose:5433). Использует fakeredis для Redis.
Каждый тест получает свежий движок и очищает telegram_config / telegram_recipients
/ telegram_invites после выполнения.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import fakeredis.aioredis as fakeredis_aio  # type: ignore[import-not-found]
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import get_engine, get_redis
from apps.api.main import create_app

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def fake_redis():
    """Fakeredis async — без живого Redis-сервера."""
    return fakeredis_aio.FakeRedis()


@pytest_asyncio.fixture
async def app_client(pg_engine, fake_redis):
    """AsyncClient с FastAPI app, подключённым к реальному Postgres + fakeredis."""
    app = create_app()
    app.dependency_overrides[get_engine] = lambda: pg_engine
    app.dependency_overrides[get_redis] = lambda: fake_redis
    app.state.redis = fake_redis

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client

    # Очистка таблиц после теста.
    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM telegram_invites"))
        await conn.execute(text("DELETE FROM telegram_recipients"))
        await conn.execute(text("DELETE FROM telegram_config"))


# ---------------------------------------------------------------------------
# GET /settings/telegram
# ---------------------------------------------------------------------------


# Без config — все compute-поля False/OFFLINE/None
@pytest.mark.asyncio
async def test_get_telegram_no_config_returns_defaults(app_client) -> None:
    resp = await app_client.get("/api/settings/telegram")
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_authorized"] is False
    assert data["poller_status"] == "OFFLINE"
    assert data["bot_username"] is None
    assert data["auth_deep_link"] is None
    assert data["activation_command"] == "/start auth"
    assert data["chat_id"] is None


# После PUT /token — is_authorized=True, bot_token_encrypted НЕ возвращается
@pytest.mark.asyncio
async def test_put_token_then_get_is_authorized(app_client) -> None:
    # Мокаем bot_username=None (getMe не нужен в тесте)
    with patch(
        "apps.api.routers.v1.settings_telegram.compute_bot_username",
        new=AsyncMock(return_value=None),
    ):
        resp = await app_client.put(
            "/api/settings/telegram/token",
            json={"bot_token": "1234567890:TEST_TOKEN"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_authorized"] is True
    # bot_token_encrypted не должен попасть в ответ
    assert "bot_token_encrypted" not in data


# PUT /token, затем GET — is_authorized=True
@pytest.mark.asyncio
async def test_get_after_put_token_shows_authorized(app_client) -> None:
    with patch(
        "apps.api.routers.v1.settings_telegram.compute_bot_username",
        new=AsyncMock(return_value=None),
    ):
        await app_client.put(
            "/api/settings/telegram/token",
            json={"bot_token": "9876543210:ANOTHER_TOKEN"},
        )
        resp = await app_client.get("/api/settings/telegram")
    assert resp.status_code == 200
    assert resp.json()["is_authorized"] is True


# DELETE /settings/telegram — после GET is_authorized=False
@pytest.mark.asyncio
async def test_delete_telegram_clears_token(app_client) -> None:
    with patch(
        "apps.api.routers.v1.settings_telegram.compute_bot_username",
        new=AsyncMock(return_value=None),
    ):
        # Создаём токен
        await app_client.put(
            "/api/settings/telegram/token",
            json={"bot_token": "111:TOKEN"},
        )
        # Удаляем
        resp = await app_client.delete("/api/settings/telegram")
        assert resp.status_code == 200
        assert resp.json()["is_authorized"] is False

        # GET подтверждает
        resp2 = await app_client.get("/api/settings/telegram")
        assert resp2.status_code == 200
        assert resp2.json()["is_authorized"] is False


# compute_poller_status: ONLINE при свежем heartbeat
@pytest.mark.asyncio
async def test_get_telegram_online_poller_status(pg_engine, fake_redis) -> None:
    # Вставляем строку с poller_heartbeat_at = 10 секунд назад
    now = datetime.now(UTC)
    heartbeat = now - timedelta(seconds=10)

    async with pg_engine.begin() as conn:
        await conn.execute(
            text("""
                INSERT INTO telegram_config
                  (singleton_key, bot_token_encrypted, poller_heartbeat_at)
                VALUES ('default', :tok, :hb)
                ON CONFLICT (singleton_key) DO UPDATE
                  SET bot_token_encrypted = EXCLUDED.bot_token_encrypted,
                      poller_heartbeat_at = EXCLUDED.poller_heartbeat_at
            """),
            {"tok": "gAAAAABencrpted", "hb": heartbeat},
        )

    app = create_app()
    app.dependency_overrides[get_engine] = lambda: pg_engine
    app.dependency_overrides[get_redis] = lambda: fake_redis
    app.state.redis = fake_redis

    with patch(
        "apps.api.routers.v1.settings_telegram.compute_bot_username",
        new=AsyncMock(return_value=None),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/settings/telegram")

    assert resp.status_code == 200
    assert resp.json()["poller_status"] == "ONLINE"

    # Очистка
    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM telegram_config"))


# compute_poller_status: OFFLINE при старом heartbeat
@pytest.mark.asyncio
async def test_get_telegram_offline_poller_status(pg_engine, fake_redis) -> None:
    # Вставляем строку с poller_heartbeat_at = 5 минут назад
    heartbeat = datetime.now(UTC) - timedelta(minutes=5)

    async with pg_engine.begin() as conn:
        await conn.execute(
            text("""
                INSERT INTO telegram_config
                  (singleton_key, bot_token_encrypted, poller_heartbeat_at)
                VALUES ('default', :tok, :hb)
                ON CONFLICT (singleton_key) DO UPDATE
                  SET bot_token_encrypted = EXCLUDED.bot_token_encrypted,
                      poller_heartbeat_at = EXCLUDED.poller_heartbeat_at
            """),
            {"tok": "gAAAAABencrpted", "hb": heartbeat},
        )

    app = create_app()
    app.dependency_overrides[get_engine] = lambda: pg_engine
    app.dependency_overrides[get_redis] = lambda: fake_redis
    app.state.redis = fake_redis

    with patch(
        "apps.api.routers.v1.settings_telegram.compute_bot_username",
        new=AsyncMock(return_value=None),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/settings/telegram")

    assert resp.status_code == 200
    assert resp.json()["poller_status"] == "OFFLINE"

    # Очистка
    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM telegram_config"))


# ---------------------------------------------------------------------------
# GET /settings/telegram/recipients
# ---------------------------------------------------------------------------


# GET recipients — видит только non-revoked
@pytest.mark.asyncio
async def test_get_recipients_returns_only_non_revoked(pg_engine, fake_redis) -> None:
    # Вставляем двух получателей: один активный, один revoked
    active_id = uuid.uuid4()
    revoked_id = uuid.uuid4()
    now = datetime.now(UTC)

    async with pg_engine.begin() as conn:
        await conn.execute(
            text("""
                INSERT INTO telegram_recipients (id, chat_id, telegram_user_id, role, created_at)
                VALUES (:id1, 100, 200, 'owner', :now)
            """),
            {"id1": active_id, "now": now},
        )
        await conn.execute(
            text("""
                INSERT INTO telegram_recipients
                  (id, chat_id, telegram_user_id, role, created_at, revoked_at)
                VALUES (:id2, 101, 201, 'recipient', :now, :rev)
            """),
            {"id2": revoked_id, "now": now, "rev": now},
        )

    app = create_app()
    app.dependency_overrides[get_engine] = lambda: pg_engine
    app.dependency_overrides[get_redis] = lambda: fake_redis
    app.state.redis = fake_redis

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/api/settings/telegram/recipients")

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["recipients"][0]["id"] == str(active_id)

    # Очистка
    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM telegram_recipients"))


# DELETE /recipients/{id} — soft-delete, revoked_at выставлен
@pytest.mark.asyncio
async def test_delete_recipient_soft_delete(pg_engine, fake_redis) -> None:
    r_id = uuid.uuid4()
    now = datetime.now(UTC)

    async with pg_engine.begin() as conn:
        await conn.execute(
            text("""
                INSERT INTO telegram_recipients (id, chat_id, telegram_user_id, role, created_at)
                VALUES (:id, 100, 200, 'owner', :now)
            """),
            {"id": r_id, "now": now},
        )

    app = create_app()
    app.dependency_overrides[get_engine] = lambda: pg_engine
    app.dependency_overrides[get_redis] = lambda: fake_redis
    app.state.redis = fake_redis

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.delete(f"/api/settings/telegram/recipients/{r_id}")
        assert resp.status_code == 200

        # Проверяем, что revoked_at теперь выставлен
        async with AsyncSession(pg_engine) as session:
            row_result = await session.execute(
                text("SELECT revoked_at FROM telegram_recipients WHERE id = :id"),
                {"id": r_id},
            )
            row = row_result.first()
        assert row is not None
        assert row[0] is not None  # revoked_at заполнен

    # Очистка
    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM telegram_recipients"))


# DELETE несуществующего получателя → 404
@pytest.mark.asyncio
async def test_delete_nonexistent_recipient_returns_404(app_client) -> None:
    fake_id = uuid.uuid4()
    resp = await app_client.delete(f"/api/settings/telegram/recipients/{fake_id}")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /settings/telegram/recipients/invite
# ---------------------------------------------------------------------------


# POST invite — возвращает code и создаёт строку в telegram_invites
@pytest.mark.asyncio
async def test_post_invite_creates_code(pg_engine, fake_redis) -> None:
    app = create_app()
    app.dependency_overrides[get_engine] = lambda: pg_engine
    app.dependency_overrides[get_redis] = lambda: fake_redis
    app.state.redis = fake_redis

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post("/api/settings/telegram/recipients/invite")

    assert resp.status_code == 200
    data = resp.json()
    assert "code" in data
    assert len(data["code"]) > 0
    assert "expires_at" in data

    # Проверяем, что строка появилась в БД
    async with pg_engine.begin() as conn:
        result = await conn.execute(
            text("SELECT code FROM telegram_invites WHERE code = :code"),
            {"code": data["code"]},
        )
        assert result.first() is not None

    # Очистка
    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM telegram_invites"))


# Два вызова POST invite — коды разные (secrets.token_urlsafe)
@pytest.mark.asyncio
async def test_post_invite_codes_are_unique(app_client) -> None:
    # Первый invite
    resp1 = await app_client.post("/api/settings/telegram/recipients/invite")
    assert resp1.status_code == 200
    # Второй invite
    resp2 = await app_client.post("/api/settings/telegram/recipients/invite")
    assert resp2.status_code == 200
    # Коды должны отличаться
    assert resp1.json()["code"] != resp2.json()["code"]
