# -*- coding: utf-8 -*-
"""Общие fixtures для интеграционных тестов.

Поднимает «фейковые» внешние сервисы:
- `pg_engine`          — async engine к реальному Postgres из docker-compose:5433
- `tg_respx`           — respx-mock для api.telegram.org (TG Bot API)
- `fake_redis_client`  — fakeredis async (без живого Redis)
- `fake_ad_lib_client` — стаб AdLibraryClient (без живого browser-agent gRPC)

Все fixtures изолированы по тестам (cleanup в teardown), поэтому можно гонять параллельно.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
import respx
from httpx import Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


def _db_url() -> str | None:
    """Resolve DB URL: TEST_DATABASE_URL → DATABASE_URL → POSTGRES_*."""
    url = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        env_vars: dict[str, str] = {}
        env_file = Path(__file__).resolve().parent.parent.parent / ".env"
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                env_vars[k.strip()] = v.strip().strip('"').strip("'")
        host = env_vars.get("POSTGRES_HOST", "127.0.0.1")
        port = env_vars.get("POSTGRES_PORT", "5432")
        db_name = env_vars.get("POSTGRES_DB")
        user = env_vars.get("POSTGRES_USER")
        password = env_vars.get("POSTGRES_PASSWORD", "")
        if not (db_name and user):
            return None
        from urllib.parse import quote_plus

        url = f"postgresql+asyncpg://{user}:{quote_plus(password)}@{host}:{port}/{db_name}"
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


@pytest_asyncio.fixture
async def pg_engine() -> AsyncEngine:
    """Async engine к Postgres из docker-compose. Skip если БД недоступна."""
    url = _db_url()
    if not url:
        pytest.skip("Нет POSTGRES_DB / DATABASE_URL — пропускаю DB-тест")
    engine = create_async_engine(url, echo=False)
    # Sanity check: можем подключиться?
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:
        pytest.skip(f"Postgres недоступен: {exc}")
    yield engine
    await engine.dispose()


# ====================== TG API mock через respx ======================


@dataclass
class TgRespxRecorder:
    """Записывает все исходящие вызовы к TG Bot API + позволяет программировать ответы."""

    sent_messages: list[dict[str, Any]] = field(default_factory=list)
    answered_callbacks: list[dict[str, Any]] = field(default_factory=list)
    fetched_updates_count: int = 0
    queued_updates: list[dict[str, Any]] = field(default_factory=list)


@pytest.fixture
def tg_respx():
    """Mock всех HTTP-запросов к api.telegram.org.

    Возвращает recorder где собираются все send_message / answer_callback вызовы.
    По умолчанию getUpdates возвращает пустой список — переопределяй через
    `tg_respx.queued_updates = [...]` для конкретного теста.

    Использование:
        async def test_smth(tg_respx):
            ...
            assert len(tg_respx.sent_messages) == 1
            assert tg_respx.sent_messages[0]["text"] == "..."
    """
    rec = TgRespxRecorder()

    with respx.mock(assert_all_called=False) as mock:
        # sendMessage
        def _send_message_handler(request):
            import json as _json

            payload = _json.loads(request.content) if request.content else {}
            rec.sent_messages.append(payload)
            return Response(
                200,
                json={
                    "ok": True,
                    "result": {
                        "message_id": 1000 + len(rec.sent_messages),
                        "chat": {"id": payload.get("chat_id"), "type": "private"},
                        "date": 0,
                        "text": payload.get("text", ""),
                    },
                },
            )

        mock.post(url__regex=r"https://api\.telegram\.org/bot[^/]+/sendMessage").mock(
            side_effect=_send_message_handler
        )

        # getUpdates — возвращает то что в queued_updates, потом пусто
        def _get_updates_handler(request):
            rec.fetched_updates_count += 1
            result = list(rec.queued_updates)
            rec.queued_updates.clear()
            return Response(200, json={"ok": True, "result": result})

        mock.post(url__regex=r"https://api\.telegram\.org/bot[^/]+/getUpdates").mock(
            side_effect=_get_updates_handler
        )

        # answerCallbackQuery
        def _answer_cb_handler(request):
            import json as _json

            payload = _json.loads(request.content) if request.content else {}
            rec.answered_callbacks.append(payload)
            return Response(200, json={"ok": True, "result": True})

        mock.post(url__regex=r"https://api\.telegram\.org/bot[^/]+/answerCallbackQuery").mock(
            side_effect=_answer_cb_handler
        )

        # setMyCommands / deleteMyCommands / setChatMenuButton / любой другой —
        # отвечаем 200/ok чтобы не падать в инициализации
        mock.post(url__regex=r"https://api\.telegram\.org/bot[^/]+/\w+").mock(
            return_value=Response(200, json={"ok": True, "result": True})
        )

        yield rec


# ====================== Fake Redis (fakeredis) ======================


@pytest_asyncio.fixture
async def fake_redis_client():
    """In-memory Redis через fakeredis. Совместим с redis.asyncio.Redis API."""
    try:
        import fakeredis.aioredis  # type: ignore
    except ImportError:
        pytest.skip("fakeredis не установлен")
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    try:
        yield client
    finally:
        await client.aclose()


# ====================== Fake AdLibraryClient (gRPC stub) ======================


@dataclass
class FakeAdLibraryScenario:
    """Программируемый сценарий ответа от fake-gRPC-клиента."""

    ad_count: int = 0
    ads: list[dict[str, Any]] = field(default_factory=list)
    duration_ms: int = 1234
    pages_fetched: int = 1
    raise_error: Exception | None = None


@pytest.fixture
def fake_ad_lib_scenario() -> FakeAdLibraryScenario:
    """Программируемый сценарий: тест задаёт ads/ошибку до запуска pipeline."""
    return FakeAdLibraryScenario()


@pytest.fixture
def fake_ad_lib_client(fake_ad_lib_scenario, monkeypatch):
    """Подменяет AdLibraryClient в scanner.py — без живого browser-agent.

    Использование:
        async def test_smth(fake_ad_lib_client, fake_ad_lib_scenario):
            fake_ad_lib_scenario.ad_count = 5
            fake_ad_lib_scenario.ads = [{"ad_archive_id": "1"}, ...]
            # ... run scanner
    """
    from clients.python_grpc.ad_library_client import AdLibrarySearchResult

    class _FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def start(self):
            return None

        async def close(self):
            return None

        async def search_ads(self, *, country, query, search_type, max_pages, **_kw):
            if fake_ad_lib_scenario.raise_error is not None:
                raise fake_ad_lib_scenario.raise_error
            return AdLibrarySearchResult(
                ad_count=fake_ad_lib_scenario.ad_count,
                ads=fake_ad_lib_scenario.ads,
                duration_ms=fake_ad_lib_scenario.duration_ms,
                pages_fetched=fake_ad_lib_scenario.pages_fetched,
                raw_json="[]",
            )

    monkeypatch.setattr(
        "core.ad_library.scanner.AdLibraryClient",
        _FakeClient,
    )
    return _FakeClient


# ====================== Test data cleanup ======================


@pytest_asyncio.fixture
async def clean_ad_library_tables(pg_engine):
    """Очищает ad_library_* таблицы до и после теста.

    Не трогает settings/catalog — те живут на уровне сессии БД.
    """
    tables_in_order = [
        "ad_library_report",
        "ad_library_tier",
        "ad_library_media",
        "ad_library_winner_archive",
        "ad_library_snapshot",
        "ad_library_ad",
        "ad_library_scan",
    ]

    async def _truncate():
        async with pg_engine.begin() as conn:
            for t in tables_in_order:
                await conn.execute(text(f"DELETE FROM {t}"))

    await _truncate()
    yield
    await _truncate()


@pytest_asyncio.fixture
async def seeded_telegram_config(pg_engine):
    """UPSERT telegram_config с токеном, зашифрованным ТЕКУЩИМ ENCRYPTION_KEY.

    Нужно потому что в БД может лежать blob от старого ключа (рассинхрон при ротации),
    из-за которого load_telegram_config вернёт None и dispatch скипнет отправку.
    Cleanup — DELETE всей строки, чтобы не оставлять fake token между тестами.
    """
    from core.crypto import encrypt

    enc = encrypt("TEST_BOT_TOKEN_FAKE")
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO telegram_config
                    (singleton_key, bot_token_encrypted, chat_id,
                     forum_warning_thread_id, forum_stop_thread_id, poller_offset)
                VALUES ('default', :tok, -1001234567890, 11, 22, 0)
                ON CONFLICT (singleton_key) DO UPDATE
                SET bot_token_encrypted = EXCLUDED.bot_token_encrypted,
                    chat_id = EXCLUDED.chat_id,
                    forum_warning_thread_id = EXCLUDED.forum_warning_thread_id,
                    forum_stop_thread_id = EXCLUDED.forum_stop_thread_id,
                    updated_at = NOW()
                """
            ),
            {"tok": enc},
        )
    yield {
        "chat_id": -1001234567890,
        "forum_warning_thread_id": 11,
        "forum_stop_thread_id": 22,
    }
    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM telegram_config WHERE singleton_key = 'default'"))


@pytest_asyncio.fixture
async def fb_ad_fixture(pg_engine):
    """Создаёт offer→campaign→adset→ad для тестов которым нужен реальный fb_ads.id.

    Cleanup в teardown по cascade от offers.
    """
    offer_id = uuid.uuid4()
    campaign_id = uuid.uuid4()
    adset_id = uuid.uuid4()
    ad_id = uuid.uuid4()
    suffix = uuid.uuid4().hex[:8]

    async with pg_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO offers (id, code, name) VALUES (:i, :c, :n)"),
            {"i": offer_id, "c": f"TST_{suffix}", "n": f"Test offer {suffix}"},
        )
        await conn.execute(
            text(
                """
                INSERT INTO fb_campaigns (id, campaign_name, offer_id)
                VALUES (:i, :n, :o)
                """
            ),
            {"i": campaign_id, "n": f"CMP_{suffix}", "o": offer_id},
        )
        await conn.execute(
            text("INSERT INTO fb_adsets (id, campaign_id, adset_name) VALUES (:i, :c, :n)"),
            {"i": adset_id, "c": campaign_id, "n": f"ADSET_{suffix}"},
        )
        await conn.execute(
            text("INSERT INTO fb_ads (id, adset_id, fb_ad_id, ad_name) VALUES (:i, :a, :f, :n)"),
            {"i": ad_id, "a": adset_id, "f": f"23000000{suffix}", "n": f"AD_{suffix}"},
        )

    yield MagicMock(
        offer_id=offer_id,
        campaign_id=campaign_id,
        adset_id=adset_id,
        ad_id=ad_id,
    )

    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM offers WHERE id = :i"), {"i": offer_id})
