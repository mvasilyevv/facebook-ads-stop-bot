# -*- coding: utf-8 -*-
"""Интеграционные тесты GET /api/dashboard/stats.

Требуется реальный Postgres из docker-compose. Перед каждым тестом
очищаем тестовые offers/campaigns/ads/metrics/scan_runs — изолируем тесты.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from apps.api.deps import get_engine, get_redis
from apps.api.main import create_app


def _make_app(engine=None, redis=None):
    """Собираем FastAPI с подменёнными engine/redis для теста."""
    app = create_app()
    if engine is not None:
        app.dependency_overrides[get_engine] = lambda: engine
    if redis is not None:
        app.dependency_overrides[get_redis] = lambda: redis
        app.state.redis = redis
    return app


@pytest_asyncio.fixture
async def clean_stats(pg_engine):
    """Очистка таблиц перед/после теста. Cleanup task_queue по requested_by LIKE."""

    async def _wipe():
        async with pg_engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM ad_metrics WHERE cycle_ts >= NOW() - INTERVAL '30 days'")
            )
            await conn.execute(text("DELETE FROM ad_alert_state"))
            await conn.execute(text("DELETE FROM fb_ads"))
            await conn.execute(text("DELETE FROM fb_adsets"))
            await conn.execute(text("DELETE FROM fb_campaigns"))
            await conn.execute(text("DELETE FROM offers WHERE code LIKE 'STATS_%'"))
            await conn.execute(
                text("DELETE FROM scan_runs WHERE started_at >= NOW() - INTERVAL '30 days'")
            )
            await conn.execute(text("DELETE FROM task_queue WHERE requested_by LIKE 'stats_%'"))

    await _wipe()
    yield
    await _wipe()


async def _seed_ad(conn, suffix: str, alert_state: str | None = None, delivery_status=None):
    """Создаёт offer→campaign→adset→ad. Возвращает ad_id.

    delivery_status — статус доставки в FB (ACTIVE/OFF/…); None = NULL (как раньше).
    """
    offer_id = uuid.uuid4()
    campaign_id = uuid.uuid4()
    adset_id = uuid.uuid4()
    ad_id = uuid.uuid4()
    fb_ad_id = f"77{suffix}"

    await conn.execute(
        text("INSERT INTO offers (id, code, name) VALUES (:i, :c, :n)"),
        {"i": offer_id, "c": f"STATS_{suffix}", "n": f"Stats offer {suffix}"},
    )
    await conn.execute(
        text("INSERT INTO fb_campaigns (id, campaign_name, offer_id) VALUES (:i, :n, :o)"),
        {"i": campaign_id, "n": f"STATS_CMP_{suffix}", "o": offer_id},
    )
    await conn.execute(
        text("INSERT INTO fb_adsets (id, campaign_id, adset_name) VALUES (:i, :c, :n)"),
        {"i": adset_id, "c": campaign_id, "n": f"STATS_ADS_{suffix}"},
    )
    await conn.execute(
        text(
            "INSERT INTO fb_ads (id, adset_id, fb_ad_id, ad_name, is_active, delivery_status) "
            "VALUES (:i, :a, :f, :n, true, :ds)"
        ),
        {
            "i": ad_id,
            "a": adset_id,
            "f": fb_ad_id,
            "n": f"STATS_AD_{suffix}",
            "ds": delivery_status,
        },
    )
    if alert_state:
        await conn.execute(
            text(
                "INSERT INTO ad_alert_state (ad_id, alert_state, current_stage) "
                "VALUES (:a, :s, :cs)"
            ),
            {
                "a": ad_id,
                "s": alert_state,
                "cs": "warning"
                if alert_state == "warning_sent"
                else ("stop" if alert_state == "stop_sent" else None),
            },
        )
    return ad_id


# Тест: пустая БД → все 0, observer_status='unknown'.
@pytest.mark.asyncio
async def test_stats_empty_db(pg_engine, fake_redis_client, clean_stats) -> None:
    """Пустая БД → все счётчики 0, observer_status='unknown'."""
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/dashboard/stats")

    assert resp.status_code == 200
    data = resp.json()
    # На общей БД могут быть scans/tasks от других тестов — проверяем что наши счётчики 0
    assert data["observer_status"] == "unknown"
    # Гарантированно 0 после очистки в clean_stats — но не trust на other tests'ы,
    # поэтому проверяем только что наш слепок данных принципиально пуст.
    # ads_in_warning/stop принципиально 0 (мы очистили ad_alert_state)
    assert data["ads_in_warning"] == 0
    assert data["ads_in_stop"] == 0
    assert data["ads_in_claimed"] == 0
    assert data["ads_in_disabled"] == 0
    assert data["active_incidents"] == 0


# Тест: 5 ads с разными alert_state → counts точно совпадают (нет double-count от JOIN).
@pytest.mark.asyncio
async def test_stats_counts_ads_by_state(pg_engine, fake_redis_client, clean_stats) -> None:
    """Ровно 2 warning + 1 stop засеяно → ровно 2/1/3 в ответе.

    Тест ловит double-count при JOIN fan-out: если LEFT JOIN на ad_metrics
    или другую таблицу дублирует строки ad_alert_state, счётчики будут > наших N.
    clean_stats гарантирует изоляцию (DELETE всех тест-строк до/после).
    """
    async with pg_engine.begin() as conn:
        await _seed_ad(conn, "N1")  # normal (нет строки в ad_alert_state)
        await _seed_ad(conn, "N2")  # normal
        await _seed_ad(conn, "W1", alert_state="warning_sent")
        await _seed_ad(conn, "W2", alert_state="warning_sent")
        await _seed_ad(conn, "S1", alert_state="stop_sent")

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/dashboard/stats")

    assert resp.status_code == 200
    data = resp.json()
    # Точное равенство: double-count от JOIN fan-out сразу виден как > 2/1/3.
    assert data["ads_in_warning"] == 2, (
        f"Ожидалось 2 warning, получено {data['ads_in_warning']} — возможен double-count"
    )
    assert data["ads_in_stop"] == 1, (
        f"Ожидалось 1 stop, получено {data['ads_in_stop']} — возможен double-count"
    )
    # active_incidents = warning_sent + stop_sent без snooze = 2 + 1 = 3
    assert data["active_incidents"] == 3, (
        f"Ожидалось 3 active_incidents, получено {data['active_incidents']} — "
        "возможен double-count или snooze-фильтр сломан"
    )


# Тест: alert_state=normal, но delivery_status=OFF → панель считает «отключённым», не «нормой»
# (согласовано с UI displayAdState). ACTIVE-normal остаётся нормой. Delta-подход — устойчив к
# чужим строкам в общей БД.
@pytest.mark.asyncio
async def test_stats_off_delivery_counts_as_disabled(
    pg_engine, fake_redis_client, clean_stats
) -> None:
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        before = (await ac.get("/api/dashboard/stats")).json()
        async with pg_engine.begin() as conn:
            await _seed_ad(conn, "OFFNORM", alert_state=None, delivery_status="OFF")
            await _seed_ad(conn, "ACTNORM", alert_state=None, delivery_status="ACTIVE")
        after = (await ac.get("/api/dashboard/stats")).json()

    # OFF при alert=normal → в disabled, ACTIVE → в normal. Проверяем дельту (а не абсолют).
    assert after["ads_in_normal"] - before["ads_in_normal"] == 1, (
        "ACTIVE-normal должно дать +1 норму"
    )
    assert after["ads_in_disabled"] - before["ads_in_disabled"] == 1, (
        "OFF-объявление при alert=normal должно уйти в disabled, а не в норму"
    )


# Тест: пустой (empty) скан НЕ обнуляет «под контролем» — scope по последнему success.
@pytest.mark.asyncio
async def test_stats_empty_scan_does_not_collapse_scope(
    pg_engine, fake_redis_client, clean_stats
) -> None:
    """Регресс: последний скан `empty` (транзиентная слепота) не должен схлопывать
    дашборд в 0. scope.since считается по последнему SUCCESS-скану (реально видел
    объявления), `empty` границу не двигает.

    Раскладка времени: success-скан в T-120с (видел объявления, last_seen→T-90с),
    затем empty-скан в T-30с (САМЫЙ свежий, ничего не увидел). Старая логика
    (scope = success+empty) брала бы границу T-30с → last_seen T-90с < T-30с →
    объявление выпадает → total_ads_monitored=0. Новая (success only) → граница
    T-120с → объявление в окне → total_ads_monitored=1.
    """
    now = datetime.now(UTC)
    async with pg_engine.begin() as conn:
        ad_id = await _seed_ad(conn, "EMPTYSCOPE")  # normal (нет alert_state)
        await conn.execute(
            text("UPDATE fb_ads SET last_seen_at = :ls WHERE id = :i"),
            {"ls": now - timedelta(seconds=90), "i": ad_id},
        )
        # success-скан РАНЬШЕ (реально видел объявления)
        await conn.execute(
            text(
                "INSERT INTO scan_runs (scan_id, started_at, finished_at, outcome, duration_ms) "
                "VALUES (:s, :st, :ft, 'success', 100)"
            ),
            {"s": 92001, "st": now - timedelta(seconds=120), "ft": now - timedelta(seconds=118)},
        )
        # empty-скан ПОЗЖЕ (самый свежий, ничего не увидел) — НЕ должен двигать scope
        await conn.execute(
            text(
                "INSERT INTO scan_runs (scan_id, started_at, finished_at, outcome, duration_ms) "
                "VALUES (:s, :st, :ft, 'empty', 100)"
            ),
            {"s": 92002, "st": now - timedelta(seconds=30), "ft": now - timedelta(seconds=28)},
        )

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/dashboard/stats")

    assert resp.status_code == 200
    data = resp.json()
    # Под старой логикой было бы 0 — объявление осталось под контролем.
    assert data["total_ads_monitored"] == 1, (
        f"Ожидалось 1 под контролем, получено {data['total_ads_monitored']} — "
        "empty-скан схлопнул scope (баг)"
    )
    assert data["ads_in_normal"] == 1


# Тест: last_scan_at корректно подхватывается из самого свежего scan_run.
@pytest.mark.asyncio
async def test_stats_last_scan_from_scan_runs(pg_engine, fake_redis_client, clean_stats) -> None:
    """last_scan_at и last_scan_outcome берутся из самого свежего scan_run."""
    now = datetime.now(UTC)
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO scan_runs (scan_id, started_at, outcome, duration_ms) "
                "VALUES (:s1, :t1, 'ok', 1234)"
            ),
            {"s1": 90001, "t1": now - timedelta(minutes=10)},
        )
        await conn.execute(
            text(
                "INSERT INTO scan_runs (scan_id, started_at, outcome, duration_ms) "
                "VALUES (:s2, :t2, 'error', 4567)"
            ),
            # Самый свежий — он должен оказаться last_scan_at
            {"s2": 90002, "t2": now - timedelta(minutes=1)},
        )

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/dashboard/stats")

    assert resp.status_code == 200
    data = resp.json()
    assert data["last_scan_at"] is not None
    assert data["last_scan_outcome"] == "error"  # самый свежий


# Тест: observer_status вытащен из Redis.
@pytest.mark.asyncio
async def test_stats_observer_status_from_redis(pg_engine, fake_redis_client, clean_stats) -> None:
    """observer:runtime в Redis → observer_status совпадает."""
    await fake_redis_client.set(
        "observer:runtime",
        json.dumps({"status": "running", "last_scan_at": datetime.now(UTC).isoformat()}),
    )

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/dashboard/stats")

    assert resp.status_code == 200
    data = resp.json()
    assert data["observer_status"] == "running"


# Тест: pending_disable_tasks учитывает draft/pending/retrying (diff-подход).
@pytest.mark.asyncio
async def test_stats_pending_disable_tasks(pg_engine, fake_redis_client, clean_stats) -> None:
    """draft/pending/retrying → +3 к baseline; succeeded → не меняет счётчик.

    Diff-подход: snapshot ДО → засеять 3 pending + 1 succeeded → snapshot ПОСЛЕ.
    Ожидаем рост ровно на 3 (не на 4). Isolates from other tests in suite.
    """
    app = _make_app(engine=pg_engine, redis=fake_redis_client)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        baseline_resp = await ac.get("/api/dashboard/stats")
    assert baseline_resp.status_code == 200
    baseline_pending = baseline_resp.json()["pending_disable_tasks"]

    async with pg_engine.begin() as conn:
        # 3 pending-статуса — все должны попасть в счётчик.
        for status in ("pending", "retrying", "draft"):
            await conn.execute(
                text(
                    "INSERT INTO task_queue (task_type, status, idempotency_key, "
                    "payload, requested_by) VALUES "
                    "('disable', :st, :ik, '{}'::jsonb, 'stats_test')"
                ),
                {"st": status, "ik": f"stats_disable_{status}_{uuid.uuid4().hex[:6]}"},
            )
        # succeeded НЕ должен попасть в pending_disable_tasks.
        await conn.execute(
            text(
                "INSERT INTO task_queue (task_type, status, idempotency_key, "
                "payload, requested_by) VALUES "
                "('disable', 'succeeded', :ik, '{}'::jsonb, 'stats_test')"
            ),
            {"ik": f"stats_disable_ok_{uuid.uuid4().hex[:6]}"},
        )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        after_resp = await ac.get("/api/dashboard/stats")

    assert after_resp.status_code == 200
    after_pending = after_resp.json()["pending_disable_tasks"]

    # Рост ровно на 3: draft+pending+retrying попали, succeeded — нет.
    assert after_pending - baseline_pending == 3, (
        f"Ожидался рост на 3 (draft+pending+retrying), "
        f"получено +{after_pending - baseline_pending} "
        f"(baseline={baseline_pending}, after={after_pending}) — "
        "succeeded мог попасть в счётчик или double-count"
    )


# Тест: failed_tasks_24h — только последние 24h (diff-подход для изоляции от других тестов).
@pytest.mark.asyncio
async def test_stats_failed_tasks_24h_window(pg_engine, fake_redis_client, clean_stats) -> None:
    """Failed-задача старше 24h не меняет счётчик; свежая — увеличивает ровно на 1.

    Стратегия diff: /stats читается ДО засева, затем ПОСЛЕ двух вставок (fresh + old).
    Свежая failed → счётчик вырос на +1. Старая (48h) → счётчик не вырос сверх +1.
    Это изолирует от других тестов suite которые тоже оставляют failed tasks в БД.
    """
    app = _make_app(engine=pg_engine, redis=fake_redis_client)

    # Snapshot ДО засева — базовый уровень failed_tasks_24h в текущей БД.
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        baseline_resp = await ac.get("/api/dashboard/stats")
    assert baseline_resp.status_code == 200
    baseline_count = baseline_resp.json()["failed_tasks_24h"]

    async with pg_engine.begin() as conn:
        # 1 failed СЕЙЧАС — должен прибавить +1 к baseline.
        await conn.execute(
            text(
                "INSERT INTO task_queue (task_type, status, idempotency_key, "
                "payload, requested_by, updated_at) VALUES "
                "('disable', 'failed', :ik, '{}'::jsonb, 'stats_test', NOW())"
            ),
            {"ik": f"stats_failed_now_{uuid.uuid4().hex[:6]}"},
        )
        # 1 failed 48h назад — НЕ должен прибавить к счётчику.
        await conn.execute(
            text(
                "INSERT INTO task_queue (task_type, status, idempotency_key, "
                "payload, requested_by, updated_at) VALUES "
                "('disable', 'failed', :ik, '{}'::jsonb, 'stats_test', "
                "NOW() - INTERVAL '48 hours')"
            ),
            {"ik": f"stats_failed_old_{uuid.uuid4().hex[:6]}"},
        )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        after_resp = await ac.get("/api/dashboard/stats")

    assert after_resp.status_code == 200
    after_count = after_resp.json()["failed_tasks_24h"]

    # Ровно +1: только свежая failed попала, старая (48h) исключена 24h-фильтром.
    assert after_count - baseline_count == 1, (
        f"Ожидался рост на ровно 1 (только свежая failed), "
        f"получено +{after_count - baseline_count} (baseline={baseline_count}, after={after_count}) — "
        "24h-фильтр сломан или старая задача просочилась"
    )


# Тест: scans_today учитывает только today (партиционный фильтр).
@pytest.mark.asyncio
async def test_stats_scans_today_partition_filter(
    pg_engine, fake_redis_client, clean_stats
) -> None:
    """scans_today фильтруется по started_at >= today UTC."""
    now = datetime.now(UTC)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    async with pg_engine.begin() as conn:
        # 2 скана сегодня
        for i, outcome in enumerate(("ok", "error")):
            await conn.execute(
                text(
                    "INSERT INTO scan_runs (scan_id, started_at, outcome, duration_ms) "
                    "VALUES (:s, :t, :o, 100)"
                ),
                {"s": 91000 + i, "t": today_start + timedelta(minutes=5 + i), "o": outcome},
            )
        # 1 скан вчера (не должен попасть в scans_today)
        await conn.execute(
            text(
                "INSERT INTO scan_runs (scan_id, started_at, outcome, duration_ms) "
                "VALUES (:s, :t, 'ok', 100)"
            ),
            {"s": 91100, "t": today_start - timedelta(hours=2)},
        )

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/dashboard/stats")

    assert resp.status_code == 200
    data = resp.json()
    # Точно 2 сегодня и точно 1 с error.
    # >= не ловит если вчерашний скан просочился (сломанный partition filter).
    assert data["scans_today"] == 2, (
        f"Ожидалось 2 scans_today, получено {data['scans_today']} — "
        "вчерашний скан мог просочиться через сломанный partition filter"
    )
    assert data["scans_today_with_errors"] == 1, (
        f"Ожидалась 1 scans_today_with_errors, получено {data['scans_today_with_errors']}"
    )


# Тест: производительность — ответ < 1s.
@pytest.mark.asyncio
async def test_stats_performance(pg_engine, fake_redis_client, clean_stats) -> None:
    """Sanity: запрос отрабатывает быстро (< 1.5s даже на «полноценной» БД)."""
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        t0 = time.perf_counter()
        resp = await ac.get("/api/dashboard/stats")
        dt = time.perf_counter() - t0

    assert resp.status_code == 200
    assert dt < 1.5
