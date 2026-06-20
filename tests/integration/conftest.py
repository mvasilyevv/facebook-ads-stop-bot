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


def _env_pg() -> dict[str, str]:
    """POSTGRES_* из .env (+ override из окружения). Кластер один, БД выбираем сами."""
    env_vars: dict[str, str] = {}
    env_file = Path(__file__).resolve().parent.parent.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env_vars[k.strip()] = v.strip().strip('"').strip("'")
    return {
        "host": os.environ.get("POSTGRES_HOST", env_vars.get("POSTGRES_HOST", "127.0.0.1")),
        "port": os.environ.get("POSTGRES_PORT", env_vars.get("POSTGRES_PORT", "5432")),
        "db": os.environ.get("POSTGRES_DB", env_vars.get("POSTGRES_DB", "")),
        "user": os.environ.get("POSTGRES_USER", env_vars.get("POSTGRES_USER", "")),
        "password": os.environ.get("POSTGRES_PASSWORD", env_vars.get("POSTGRES_PASSWORD", "")),
    }


def _prod_db_name() -> str | None:
    """Боевое имя БД (POSTGRES_DB) — для guard против прогона тестов по проду."""
    return _env_pg()["db"] or None


def _db_name_from_url(url: str) -> str:
    return url.rsplit("/", 1)[-1].split("?", 1)[0]


def _db_url() -> str | None:
    """URL ИЗОЛИРОВАННОЙ тестовой БД. НИКОГДА не боевая POSTGRES_DB.

    Приоритет:
      1. TEST_DATABASE_URL — явный выбор оператора (на свой риск).
      2. Авто: <POSTGRES_DB>_test на тех же кредах/кластере.

    DATABASE_URL (прод-переменная воркеров) сознательно НЕ используется — был инцидент,
    когда integration-фикстуры через фолбэк на боевую БД снесли offers/offer_rules.
    Тестовая БД создаётся автоматически фикстурой _ensure_test_db (CREATE DATABASE + схема).
    """
    from urllib.parse import quote_plus

    explicit = os.environ.get("TEST_DATABASE_URL")
    if explicit:
        url = explicit
    else:
        pg = _env_pg()
        if not (pg["db"] and pg["user"]):
            return None
        test_db = f"{pg['db']}_test"
        url = (
            f"postgresql+asyncpg://{pg['user']}:{quote_plus(pg['password'])}"
            f"@{pg['host']}:{pg['port']}/{test_db}"
        )
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


def _assert_not_prod(url: str) -> None:
    """Жёсткий стоп, если тесты нацелены на боевую БД (защита от повтора инцидента)."""
    prod = _prod_db_name()
    db = _db_name_from_url(url)
    if prod and db == prod and os.environ.get("ALLOW_PROD_DB_TESTS") != "1":
        pytest.fail(
            f"Integration-тесты нацелены на БОЕВУЮ БД '{db}'. Так нельзя — фикстуры чистят "
            f"каталог. Не задавай TEST_DATABASE_URL на прод; авто-режим использует "
            f"'{db}_test'. Если осознанно — ALLOW_PROD_DB_TESTS=1.",
            pytrace=False,
        )


async def _ensure_migration_indices(eng: AsyncEngine) -> None:
    """Индексы, добавленные ТОЛЬКО миграциями (не в ORM __table_args__).

    Base.metadata.create_all создаёт индексы из ORM-моделей, но не из чистых
    миграций. На проде они накатываются alembic upgrade head; в тестовой БД
    (create_all) воссоздаём вручную.
    """
    async with eng.begin() as conn:
        # Миграция 0004: (scan_id, created_at) для partition pruning в dispatch_pending_alerts.
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_alert_events_scan_id_created "
                "ON alert_events (scan_id, created_at)"
            )
        )


async def _create_test_partitions(eng: AsyncEngine) -> None:
    """Месячные партиции на диапазон now-3..now+3 — тесты используют относительные даты
    (вчера/прошлый месяц/будущее), а apply_schema._create_first_partitions кроет лишь
    текущий+следующий месяц → CheckViolation на «no partition for row»."""
    from datetime import datetime, timezone

    from scripts.apply_schema import _PARTITIONED_TABLES, _month_bounds

    now = datetime.now(timezone.utc)
    sy, sm = now.year, now.month - 3
    while sm < 1:
        sm += 12
        sy -= 1
    months: list[tuple[int, int]] = []
    y, m = sy, sm
    for _ in range(7):  # now-3 .. now+3
        months.append((y, m))
        m += 1
        if m > 12:
            m = 1
            y += 1

    async with eng.begin() as conn:
        for table, _col in _PARTITIONED_TABLES:
            for yy, mm in months:
                fr, to = _month_bounds(yy, mm)
                part = f"{table}_{yy:04d}_{mm:02d}"
                await conn.execute(
                    text(
                        f"CREATE TABLE IF NOT EXISTS {part} PARTITION OF {table} "
                        f"FOR VALUES FROM ('{fr}') TO ('{to}')"
                    )
                )


async def _ensure_test_db_and_schema(url: str) -> None:
    """Создаёт тестовую БД на том же кластере (если нет) + разворачивает схему с партициями."""
    db_name = _db_name_from_url(url)
    # maintenance-подключение к служебной БД 'postgres' для CREATE DATABASE.
    maint_url = url.rsplit("/", 1)[0] + "/postgres"
    maint = create_async_engine(maint_url, isolation_level="AUTOCOMMIT")
    try:
        async with maint.connect() as conn:
            exists = await conn.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": db_name}
            )
            if not exists:
                await conn.execute(text(f'CREATE DATABASE "{db_name}"'))
    finally:
        await maint.dispose()

    # Схема: create_all + партиции на диапазон + seed (переиспользуем scripts/apply_schema).
    eng = create_async_engine(url)
    try:
        async with eng.connect() as conn:
            has_schema = await conn.scalar(text("SELECT to_regclass('public.offers')"))
        if not has_schema:
            from scripts.apply_schema import _create_all_tables, _seed_retention_policy

            async with eng.begin() as conn:
                await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
            await _create_all_tables(eng)
            await _create_test_partitions(eng)
            await _ensure_migration_indices(eng)
            await _seed_retention_policy(eng)
    finally:
        await eng.dispose()


@pytest.fixture(autouse=True)
def _redirect_worker_db_to_test(monkeypatch):
    """Воркеры (observer main_loop, telegram_poller) создают СВОЙ engine из
    _get_database_url() — по умолчанию БОЕВАЯ БД, в обход изолированного pg_engine.

    Тесты, запускающие main_loop с FakeGate, иначе пишут синтетику (Av01/23A001,
    'CR2 | KE | MV | promo') прямо в ПРОД — был инцидент. Перенаправляем
    _get_database_url во всех воркер-модулях на изолированную тестовую БД.
    """
    url = _db_url()
    if not url:
        return
    for mod_name in ("apps.telegram_poller.main", "apps.observer_worker.main"):
        try:
            mod = __import__(mod_name, fromlist=["_get_database_url"])
        except Exception:  # noqa: BLE001
            continue
        if hasattr(mod, "_get_database_url"):
            monkeypatch.setattr(mod, "_get_database_url", lambda: url, raising=False)


@pytest.fixture(scope="session", autouse=True)
def _ensure_test_db():
    """Session-setup: поднимает изолированную тестовую БД + схему ОДИН раз перед DB-тестами."""
    import asyncio

    url = _db_url()
    if not url:
        return  # нет кредов — DB-тесты заскипаются в pg_engine
    _assert_not_prod(url)
    try:
        asyncio.run(_ensure_test_db_and_schema(url))
    except Exception as exc:  # noqa: BLE001
        # Не валим всю сессию (мог быть недоступен кластер) — pg_engine честно заскипает.
        print(f"[conftest] не удалось поднять тестовую БД: {exc}")


@pytest_asyncio.fixture
async def pg_engine() -> AsyncEngine:
    """Async engine к ИЗОЛИРОВАННОЙ тестовой БД (<POSTGRES_DB>_test). Skip если недоступна."""
    url = _db_url()
    if not url:
        pytest.skip("Нет POSTGRES_DB / TEST_DATABASE_URL — пропускаю DB-тест")
    _assert_not_prod(url)  # двойная защита: не боевая БД
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
                    (singleton_key, bot_token_encrypted, chat_id, poller_offset)
                VALUES ('default', :tok, -1001234567890, 0)
                ON CONFLICT (singleton_key) DO UPDATE
                SET bot_token_encrypted = EXCLUDED.bot_token_encrypted,
                    chat_id = EXCLUDED.chat_id,
                    updated_at = NOW()
                """
            ),
            {"tok": enc},
        )
    yield {
        "chat_id": -1001234567890,
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
