# -*- coding: utf-8 -*-
"""Общие fixtures для интеграционных тестов.

Поднимает «фейковые» внешние сервисы:
- `pg_engine`          — async engine к явно выбранной изолированной test DB
- `tg_respx`           — respx-mock для api.telegram.org (TG Bot API)
- `fake_redis_client`  — fakeredis async (без живого Redis)

Параллельные транзакции внутри одной pytest-сессии поддерживаются. Отдельные
pytest-процессы обязаны использовать разные TEST_DATABASE_URL: session advisory
lock fail-fast защищает singleton fixtures и широкие cleanup от пересечения.
"""

from __future__ import annotations

import os
import re
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
import respx
from httpx import Response
from sqlalchemy import text
from sqlalchemy.dialects.postgresql.asyncpg import PGDialect_asyncpg
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from tests.integration.account_snapshot import (
    capture_account_snapshot_rows,
    restore_account_snapshot_rows,
)

_ALLOWED_DISPOSABLE_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "postgres"})
_SAFETY_TEST_DATABASE_PREFIX = "fb_agent_safety_test_"
_SAFETY_TEST_DATABASE_SUFFIX = "_test"
_SAFE_DATABASE_IDENTIFIER_RE = re.compile(r"[a-z][a-z0-9_]{0,62}", re.ASCII)
_ASYNC_PG_TARGET_QUERY_KEYS = frozenset(
    {
        "database",
        "dbname",
        "dsn",
        "host",
        "password",
        "port",
        "user",
        "username",
    }
)


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


def _parsed_database_url(url: str) -> URL:
    try:
        parsed = make_url(url)
    except Exception as exc:  # SQLAlchemy exposes several URL parse errors.
        raise RuntimeError("TEST_DATABASE_URL is not a valid SQLAlchemy URL") from exc
    if parsed.get_backend_name() != "postgresql":
        raise RuntimeError("TEST_DATABASE_URL must use PostgreSQL")
    if parsed.drivername not in {"postgresql", "postgresql+asyncpg"}:
        raise RuntimeError("TEST_DATABASE_URL must use the asyncpg PostgreSQL driver")
    if not parsed.username or not parsed.host or not parsed.database:
        raise RuntimeError("TEST_DATABASE_URL must include user, host and database")

    target_overrides = sorted(
        key for key in parsed.query if str(key).lower() in _ASYNC_PG_TARGET_QUERY_KEYS
    )
    if target_overrides:
        raise RuntimeError(
            "TEST_DATABASE_URL query cannot override asyncpg target fields: "
            + ", ".join(target_overrides)
        )
    if "," in parsed.host:
        raise RuntimeError("TEST_DATABASE_URL cannot use a multi-host authority")

    normalized = parsed.set(
        drivername="postgresql+asyncpg",
        port=parsed.port or 5432,
    )
    positional, connect_args = PGDialect_asyncpg().create_connect_args(normalized)
    if positional:
        raise RuntimeError("TEST_DATABASE_URL produced positional asyncpg target arguments")
    expected_target = {
        "host": normalized.host,
        "port": normalized.port,
        "database": normalized.database,
        "user": normalized.username,
        "password": normalized.password,
    }
    effective_target = {key: connect_args.get(key) for key in expected_target}
    if effective_target != expected_target:
        raise RuntimeError(
            "TEST_DATABASE_URL effective asyncpg target differs from its canonical authority"
        )
    if isinstance(connect_args.get("host"), (list, tuple)) or isinstance(
        connect_args.get("port"), (list, tuple)
    ):
        raise RuntimeError("TEST_DATABASE_URL cannot use multiple asyncpg hosts")
    return normalized


def _db_name_from_url(url: str) -> str:
    database = _parsed_database_url(url).database
    if database is None:  # pragma: no cover - guaranteed by _parsed_database_url
        raise RuntimeError("TEST_DATABASE_URL must include a database")
    return database


def _maintenance_database_url(url: str) -> URL:
    """Change only the database component and preserve every safe DSN option."""
    return _parsed_database_url(url).set(database="postgres")


def _integration_db_lock_identity(url: str) -> str:
    """Canonical cluster-local advisory key for the database actually connected."""
    return f"fb-agent:integration-pytest:{_db_name_from_url(url)}"


def _db_url() -> str | None:
    """URL ИЗОЛИРОВАННОЙ тестовой БД. НИКОГДА не боевая POSTGRES_DB.

    Приоритет:
      1. TEST_DATABASE_URL — явный выбор оператора (на свой риск).
      2. Авто: <POSTGRES_DB>_test на тех же кредах/кластере.

    DATABASE_URL (прод-переменная воркеров) сознательно НЕ используется — был инцидент,
    когда integration-фикстуры через фолбэк на боевую БД снесли offers/offer_rules.
    Тестовая БД создаётся автоматически, а схема пересобирается только после
    захвата session advisory lock.
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


def _assert_disposable_test_target(url: str) -> None:
    """Reject every target not independently recognizable as local test data."""
    parsed = _parsed_database_url(url)
    db = _db_name_from_url(url)
    host = str(parsed.host or "").lower()
    if host not in _ALLOWED_DISPOSABLE_HOSTS:
        raise RuntimeError(
            "Integration schema reset is allowed only on loopback or "
            "the local Compose postgres host"
        )
    if _SAFE_DATABASE_IDENTIFIER_RE.fullmatch(db) is None:
        raise RuntimeError(
            "Integration database must be a lowercase ASCII PostgreSQL identifier "
            "with at most 63 characters"
        )
    if not (
        db.startswith(_SAFETY_TEST_DATABASE_PREFIX) or db.endswith(_SAFETY_TEST_DATABASE_SUFFIX)
    ):
        raise RuntimeError(
            "Integration database must start with 'fb_agent_safety_test_' or end with '_test'"
        )
    prod = _prod_db_name()
    if prod and db == prod:
        raise RuntimeError(
            f"Integration database '{db}' matches runtime POSTGRES_DB; "
            "there is no destructive-test bypass"
        )


async def _create_test_partitions(eng: AsyncEngine) -> None:
    """Create extra month partitions used by tests with relative dates."""
    from datetime import datetime, timezone

    from apps.cleanup_worker.worker import _PARTITIONED

    def _month_bounds(year: int, month: int) -> tuple[str, str]:
        if month == 12:
            next_year, next_month = year + 1, 1
        else:
            next_year, next_month = year, month + 1
        return f"{year:04d}-{month:02d}-01", f"{next_year:04d}-{next_month:02d}-01"

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
        for table, _column, _policy_key in _PARTITIONED:
            for yy, mm in months:
                fr, to = _month_bounds(yy, mm)
                part = f"{table}_{yy:04d}_{mm:02d}"
                await conn.execute(
                    text(
                        f"CREATE TABLE IF NOT EXISTS {part} PARTITION OF {table} "
                        f"FOR VALUES FROM ('{fr}') TO ('{to}')"
                    )
                )


async def _ensure_test_database_exists(url: str) -> None:
    """Create the disposable database without mutating an existing schema."""
    _assert_disposable_test_target(url)
    db_name = _db_name_from_url(url)
    # maintenance-подключение к служебной БД 'postgres' для CREATE DATABASE.
    maint_url = _maintenance_database_url(url)
    maint = create_async_engine(maint_url, isolation_level="AUTOCOMMIT")
    try:
        async with maint.connect() as conn:
            exists = await conn.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": db_name}
            )
            if not exists:
                # template0 is the only clean, immutable database template.
                # template1 may contain locally installed extensions or ACL
                # drift and must never become part of baseline acceptance.
                await conn.execute(text(f'CREATE DATABASE "{db_name}" TEMPLATE template0'))
    finally:
        await maint.dispose()


async def _rebuild_test_schema(url: str) -> None:
    """Rebuild the production baseline while the session database lock is held.

    No create_all/manual seed path exists: every CI session exercises the
    migration-owned functions, views, triggers and DEFAULT partitions.
    """
    _assert_disposable_test_target(url)
    # Always rebuild from the same frozen baseline used by production.
    eng = create_async_engine(url)
    try:
        from scripts.apply_schema import _drop_and_recreate_schema, _upgrade_head

        await _drop_and_recreate_schema(eng)
        exit_code = await _upgrade_head(url)
        if exit_code != 0:
            raise RuntimeError(f"fresh Alembic baseline failed with exit code {exit_code}")
        await _create_test_partitions(eng)
    finally:
        await eng.dispose()


@pytest.fixture(autouse=True)
def _redirect_worker_db_to_test(monkeypatch):
    """Observer main_loop создаёт собственный engine из
    _get_database_url() — по умолчанию БОЕВАЯ БД, в обход изолированного pg_engine.

    Тесты, запускающие main_loop с FakeGate, иначе пишут синтетику (Av01/23A001,
    'CR2 | KE | MV | promo') прямо в ПРОД — был инцидент. Перенаправляем
    _get_database_url во всех воркер-модулях на изолированную тестовую БД.
    """
    url = _db_url()
    if not url:
        return
    for mod_name in ("apps.observer_worker.main",):
        try:
            mod = __import__(mod_name, fromlist=["_get_database_url"])
        except Exception:  # noqa: BLE001
            continue
        if hasattr(mod, "_get_database_url"):
            monkeypatch.setattr(mod, "_get_database_url", lambda: url, raising=False)


@pytest.fixture(scope="session", autouse=True)
def _ensure_test_db():
    """Create the isolated database before the exclusive session fixture starts."""
    import asyncio

    url = _db_url()
    if not url:
        return None  # нет кредов — DB-тесты заскипаются в pg_engine
    try:
        _assert_disposable_test_target(url)
        asyncio.run(_ensure_test_database_exists(url))
    except Exception as exc:  # noqa: BLE001
        setup_error = str(exc)
    else:
        return url
    pytest.fail(
        f"Не удалось подготовить изолированную integration БД: {setup_error}",
        pytrace=False,
    )


@pytest_asyncio.fixture(scope="session", autouse=True, loop_scope="session")
async def _integration_test_db_session_lock(_ensure_test_db):
    """Exclusively own one TEST_DATABASE_URL for the complete pytest session.

    Integration fixtures intentionally mutate singleton rows and some legacy
    suites still use table-wide cleanup.  A second pytest process targeting the
    same database would otherwise be able to drop the schema or delete another
    test's task between create and claim.  The dedicated connection keeps a
    PostgreSQL session advisory lock until every test and teardown has finished.
    """
    url = _ensure_test_db
    if url is None:
        yield None
        return

    db_name = _db_name_from_url(url)
    lock_identity = _integration_db_lock_identity(url)
    lock_engine = create_async_engine(url)
    lock_connection = None
    acquired = False
    try:
        lock_connection = await lock_engine.connect()
        acquired = bool(
            await lock_connection.scalar(
                text("SELECT pg_try_advisory_lock(hashtextextended(:lock_identity, 0))"),
                {"lock_identity": lock_identity},
            )
        )
        await lock_connection.commit()
        if not acquired:
            pytest.fail(
                f"Integration БД '{db_name}' уже занята другим pytest-процессом. "
                "Используй уникальный TEST_DATABASE_URL для каждого процесса/job.",
                pytrace=False,
            )

        await _rebuild_test_schema(url)
        yield lock_identity
    finally:
        if lock_connection is not None:
            if acquired:
                try:
                    await lock_connection.execute(
                        text("SELECT pg_advisory_unlock(hashtextextended(:lock_identity, 0))"),
                        {"lock_identity": lock_identity},
                    )
                    await lock_connection.commit()
                finally:
                    await lock_connection.close()
            else:
                await lock_connection.close()
        await lock_engine.dispose()


@pytest_asyncio.fixture
async def pg_engine(_integration_test_db_session_lock) -> AsyncEngine:
    """Async engine к ИЗОЛИРОВАННОЙ тестовой БД (<POSTGRES_DB>_test). Skip если недоступна."""
    url = _db_url()
    if not url:
        pytest.skip("Нет POSTGRES_DB / TEST_DATABASE_URL — пропускаю DB-тест")
    try:
        _assert_disposable_test_target(url)
    except RuntimeError as exc:
        pytest.fail(str(exc), pytrace=False)
    engine = create_async_engine(url, echo=False)
    # Sanity check: можем подключиться?
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:
        pytest.skip(f"Postgres недоступен: {exc}")
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def known_test_cabinet_timezones(pg_engine):
    """Temporarily seed explicit cabinet context without leaking authority."""
    account_ids = ("123", "111", "222")
    async with pg_engine.begin() as conn:
        previous_rows = await capture_account_snapshot_rows(conn, account_ids)
        await conn.execute(
            text(
                """
                INSERT INTO meta_account_snapshot
                    (account_id, timezone_name, currency, currency_observed_at)
                SELECT account_id, 'UTC', 'USD', NOW()
                FROM unnest(CAST(:account_ids AS text[])) AS seed(account_id)
                ON CONFLICT (account_id) DO UPDATE
                SET timezone_name = EXCLUDED.timezone_name,
                    currency = EXCLUDED.currency,
                    currency_observed_at = EXCLUDED.currency_observed_at,
                    updated_at = NOW()
                """
            ),
            {"account_ids": list(account_ids)},
        )
    try:
        yield
    finally:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    "DELETE FROM meta_account_snapshot "
                    "WHERE account_id = ANY(CAST(:account_ids AS text[]))"
                ),
                {"account_ids": list(account_ids)},
            )
            await restore_account_snapshot_rows(conn, previous_rows)


@dataclass(frozen=True)
class _BrowserReadinessSnapshot:
    vision_config: dict[str, Any] | None
    channel_readiness: dict[str, Any] | None


async def _snapshot_browser_readiness_state(pg_engine) -> _BrowserReadinessSnapshot:
    async with pg_engine.connect() as conn:
        vision_row = (
            (
                await conn.execute(
                    text(
                        """
                    SELECT x_token_encrypted, profile_id, id, singleton_key,
                           created_at, updated_at
                    FROM vision_config
                    WHERE singleton_key = 'default'
                    """
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        readiness_row = (
            (
                await conn.execute(
                    text(
                        """
                    SELECT channel, vision_config_id, vision_config_updated_at,
                           expected_profile_id, observed_profile_id,
                           observed_session_id, observed_contract_version,
                           state, reason_code, observed_at, readiness_expires_at,
                           writer_instance, generation, last_ready_at,
                           created_at, updated_at
                    FROM browser_channel_readiness
                    WHERE channel = 'meta_api'
                    """
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
    return _BrowserReadinessSnapshot(
        vision_config=(dict(vision_row) if vision_row is not None else None),
        channel_readiness=(dict(readiness_row) if readiness_row is not None else None),
    )


async def _restore_browser_readiness_state(
    pg_engine,
    snapshot: _BrowserReadinessSnapshot,
) -> None:
    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM browser_channel_readiness WHERE channel = 'meta_api'"))
        await conn.execute(text("DELETE FROM vision_config WHERE singleton_key = 'default'"))
        if snapshot.vision_config is not None:
            await conn.execute(
                text(
                    """
                    INSERT INTO vision_config
                        (x_token_encrypted, profile_id, id, singleton_key,
                         created_at, updated_at)
                    VALUES
                        (:x_token_encrypted, :profile_id, :id, :singleton_key,
                         :created_at, :updated_at)
                    """
                ),
                snapshot.vision_config,
            )
        if snapshot.channel_readiness is not None:
            await conn.execute(
                text(
                    """
                    INSERT INTO browser_channel_readiness
                        (channel, vision_config_id, vision_config_updated_at,
                         expected_profile_id, observed_profile_id,
                         observed_session_id, observed_contract_version,
                         state, reason_code, observed_at, readiness_expires_at,
                         writer_instance, generation, last_ready_at,
                         created_at, updated_at)
                    VALUES
                        (:channel, :vision_config_id, :vision_config_updated_at,
                         :expected_profile_id, :observed_profile_id,
                         :observed_session_id, :observed_contract_version,
                         :state, :reason_code, :observed_at, :readiness_expires_at,
                         :writer_instance, :generation, :last_ready_at,
                         :created_at, :updated_at)
                    """
                ),
                snapshot.channel_readiness,
            )


@asynccontextmanager
async def _fresh_browser_readiness_scope(pg_engine):
    """Publish fresh evidence and restore the exact prior authority on exit."""
    from core.meta_api.browser_readiness import (
        BrowserReadinessObservation,
        load_vision_readiness_identity,
        persist_browser_readiness,
    )

    snapshot = await _snapshot_browser_readiness_state(pg_engine)
    try:
        profile_id = f"integration-ready-{uuid.uuid4().hex[:12]}"
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO vision_config (
                      x_token_encrypted,
                      profile_id,
                      singleton_key
                    )
                    VALUES ('integration-test-not-read', :profile_id, 'default')
                    ON CONFLICT (singleton_key) DO UPDATE
                    SET x_token_encrypted = EXCLUDED.x_token_encrypted,
                        profile_id = EXCLUDED.profile_id,
                        updated_at = clock_timestamp()
                    """
                ),
                {"profile_id": profile_id},
            )
        identity = await load_vision_readiness_identity(pg_engine)
        assert identity is not None
        published = await persist_browser_readiness(
            pg_engine,
            identity=identity,
            observation=BrowserReadinessObservation(
                state="ready",
                reason_code="ready",
                observed_contract_version=5,
                observed_profile_id=profile_id,
                observed_session_id=f"integration-session-{uuid.uuid4().hex[:12]}",
            ),
            writer_instance=uuid.uuid4(),
            ttl_seconds=30,
        )
        assert published is True
        yield identity
    finally:
        await _restore_browser_readiness_state(pg_engine, snapshot)


@pytest_asyncio.fixture
async def fresh_browser_readiness(pg_engine):
    """Yield fresh v5 evidence without leaking false-green singleton state."""
    async with _fresh_browser_readiness_scope(pg_engine) as identity:
        yield identity


# ====================== TG API mock через respx ======================


@dataclass
class TgRespxRecorder:
    """Записывает все исходящие вызовы к TG Bot API + позволяет программировать ответы."""

    sent_messages: list[dict[str, Any]] = field(default_factory=list)
    answered_callbacks: list[dict[str, Any]] = field(default_factory=list)


@pytest.fixture
def tg_respx():
    """Mock всех HTTP-запросов к api.telegram.org.

    Возвращает recorder где собираются официальные HTML ``sendMessage`` и
    ``answerCallbackQuery`` вызовы.

    Использование:
        async def test_smth(tg_respx):
            ...
            assert len(tg_respx.sent_messages) == 1
            assert tg_respx.sent_messages[0]["text"] == "..."
    """
    rec = TgRespxRecorder()

    with respx.mock(assert_all_called=False) as mock:
        # Official HTML sendMessage only.
        def _send_message_handler(request):
            import json as _json

            payload = _json.loads(request.content) if request.content else {}
            recorded = dict(payload)
            method = request.url.path.rsplit("/", 1)[-1]
            recorded["_method"] = method
            rec.sent_messages.append(recorded)
            return Response(
                200,
                json={
                    "ok": True,
                    "result": {
                        "message_id": 1000 + len(rec.sent_messages),
                        "chat": {"id": payload.get("chat_id"), "type": "private"},
                        "date": 0,
                        "text": recorded.get("text", ""),
                    },
                },
            )

        mock.post(url__regex=r"https://api\.telegram\.org/bot[^/]+/sendMessage").mock(
            side_effect=_send_message_handler
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


@pytest_asyncio.fixture
async def seeded_telegram_config(pg_engine):
    """UPSERT telegram_config с токеном, зашифрованным ТЕКУЩИМ ENCRYPTION_KEY.

    Нужно потому что в БД может лежать blob от старого ключа (рассинхрон при ротации),
    из-за которого load_telegram_config вернёт None и dispatch скипнет отправку.
    Cleanup — DELETE всей строки, чтобы не оставлять fake token между тестами.
    """
    from core.crypto import encrypt
    from core.telegram.gateway import telegram_credential_fingerprint

    enc = encrypt("TEST_BOT_TOKEN_FAKE")
    fingerprint = bytes.fromhex(telegram_credential_fingerprint("TEST_BOT_TOKEN_FAKE"))
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO telegram_config
                    (singleton_key, bot_token_encrypted, bot_token_fingerprint,
                     is_enabled, webhook_generation, webhook_applied_generation,
                     webhook_operation, webhook_desired_url, webhook_state,
                     webhook_configured_at)
                VALUES ('default', :tok, :fingerprint,
                        TRUE, 1, 1, 'configure',
                        'https://test.invalid/api/v1/integrations/telegram/webhook?bot_generation=1',
                        'configured', NOW())
                ON CONFLICT (singleton_key) DO UPDATE
                SET bot_token_encrypted = EXCLUDED.bot_token_encrypted,
                    bot_token_fingerprint = EXCLUDED.bot_token_fingerprint,
                    is_enabled = TRUE,
                    webhook_generation = 1,
                    webhook_applied_generation = 1,
                    webhook_operation = 'configure',
                    webhook_desired_url = EXCLUDED.webhook_desired_url,
                    webhook_state = 'configured',
                    webhook_configured_at = NOW(),
                    updated_at = NOW()
                """
            ),
            {"tok": enc, "fingerprint": fingerprint},
        )
    yield {
        "chat_id": -1001234567890,
    }
    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM telegram_config WHERE singleton_key = 'default'"))


@pytest_asyncio.fixture
async def authoritative_telegram_config(pg_engine):
    """Configured DB authority for generation-fenced Telegram outbox tests."""
    from core.telegram.gateway import telegram_credential_fingerprint

    bot_token = "integration-telegram-authority-token"
    generation = 4242
    fingerprint = telegram_credential_fingerprint(bot_token)
    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM telegram_config WHERE singleton_key = 'default'"))
        await conn.execute(
            text(
                """
                INSERT INTO telegram_config
                    (singleton_key, bot_token_encrypted, bot_token_fingerprint,
                     is_enabled, webhook_generation,
                     webhook_applied_generation, webhook_operation,
                     webhook_state)
                VALUES
                    ('default', 'integration-test-not-decrypted',
                     :fingerprint, TRUE, :generation, :generation,
                     'configure', 'configured')
                """
            ),
            {
                "fingerprint": bytes.fromhex(fingerprint),
                "generation": generation,
            },
        )
    yield {
        "bot_token": bot_token,
        "generation": generation,
        "fingerprint": fingerprint,
    }
    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM telegram_config WHERE singleton_key = 'default'"))


@asynccontextmanager
async def _fb_ad_fixture_scope(pg_engine):
    """Create one exact catalog chain and delete only those IDs on every exit."""
    offer_id = uuid.uuid4()
    campaign_id = uuid.uuid4()
    adset_id = uuid.uuid4()
    ad_id = uuid.uuid4()
    suffix = f"{uuid.uuid4().int % 100_000_000:08d}"

    try:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text("INSERT INTO offers (id, code, name) VALUES (:i, :c, :n)"),
                {"i": offer_id, "c": f"TST_{suffix}", "n": f"Test offer {suffix}"},
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO fb_campaigns
                        (id, campaign_name, offer_id, ad_account_id)
                    VALUES (:i, :n, :o, '123')
                    """
                ),
                {"i": campaign_id, "n": f"CMP_{suffix}", "o": offer_id},
            )
            await conn.execute(
                text("INSERT INTO fb_adsets (id, campaign_id, adset_name) VALUES (:i, :c, :n)"),
                {"i": adset_id, "c": campaign_id, "n": f"ADSET_{suffix}"},
            )
            await conn.execute(
                text(
                    "INSERT INTO fb_ads (id, adset_id, fb_ad_id, ad_name) VALUES (:i, :a, :f, :n)"
                ),
                {"i": ad_id, "a": adset_id, "f": f"23000000{suffix}", "n": f"AD_{suffix}"},
            )

        yield MagicMock(
            offer_id=offer_id,
            campaign_id=campaign_id,
            adset_id=adset_id,
            ad_id=ad_id,
        )
    finally:
        async with pg_engine.begin() as conn:
            await conn.execute(text("DELETE FROM fb_ads WHERE id = :id"), {"id": ad_id})
            await conn.execute(text("DELETE FROM fb_adsets WHERE id = :id"), {"id": adset_id})
            await conn.execute(
                text("DELETE FROM fb_campaigns WHERE id = :id"),
                {"id": campaign_id},
            )
            await conn.execute(text("DELETE FROM offers WHERE id = :id"), {"id": offer_id})


@pytest_asyncio.fixture
async def fb_ad_fixture(pg_engine):
    """Yield one exact offer→campaign→adset→ad catalog chain."""
    async with _fb_ad_fixture_scope(pg_engine) as resource:
        yield resource
