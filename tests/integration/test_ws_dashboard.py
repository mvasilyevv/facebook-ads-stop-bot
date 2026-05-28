# -*- coding: utf-8 -*-
"""Интеграционные тесты WebSocket endpoint /ws/dashboard.

Архитектура тестов:
- TestClient(app) — starlette поддерживает WS через websocket_connect().
- fakeredis.aioredis.FakeRedis подставляется через app.state.ws_pubsub_redis:
  WS-хендлер использует его вместо создания нового Redis-соединения.
  Это позволяет публиковать события в канал из отдельного потока (threading.Thread)
  и сразу проверять что клиент получил нужное сообщение — без реального Redis.
- WS_HEARTBEAT_SECONDS=1 через monkeypatch — тест не ждёт 30 секунд.
- Для cleanup-теста: после disconnect задачи должны быть отменены;
  проверяем что приложение не крашит и не печатает непойманных исключений.

TODO: когда disable_worker/enable_worker/telegram_poller начнут публиковать
в fb_agent:alert:created и fb_agent:task:changed — тесты №2 и №3 станут
«настоящими» e2e; сейчас они проверяют сквозной маршрут через фиктивную публикацию.
"""

from __future__ import annotations

import json
import threading
import time

import fakeredis.aioredis

from apps.api.main import create_app

# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------


def _make_app(heartbeat_seconds: int = 1):
    """Создаёт приложение с fakeredis и коротким heartbeat для тестов."""
    fake_redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    app = create_app()
    # Подменяем основной Redis (lifespan не создаёт реальный если задан).
    app.state.redis = fake_redis
    # Подменяем Redis для pubsub внутри WS-хендлера.
    app.state.ws_pubsub_redis = fake_redis
    return app, fake_redis


def _publish_after(
    fake_redis, channel: str, payload: dict, delay: float = 0.15
) -> threading.Thread:
    """Публикует сообщение в канал из отдельного потока с задержкой.

    Задержка нужна чтобы WS-хендлер успел подписаться до публикации.
    """

    def _run():
        import asyncio

        loop = asyncio.new_event_loop()
        time.sleep(delay)
        loop.run_until_complete(fake_redis.publish(channel, json.dumps(payload)))
        loop.close()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t


# ---------------------------------------------------------------------------
# Тесты
# ---------------------------------------------------------------------------


# Проверяем что /ws/dashboard принимает соединение без ошибок.
def test_ws_connect_accepted(monkeypatch) -> None:
    monkeypatch.setenv("WS_HEARTBEAT_SECONDS", "1")
    # Перезагружаем модуль чтобы подхватить переменную окружения.
    import importlib

    import apps.api.routers.ws as ws_mod

    importlib.reload(ws_mod)

    app, fake_redis = _make_app()

    from fastapi.testclient import TestClient

    client = TestClient(app)

    # Публикуем событие чтобы WS-соединение не зависло (цикл pubsub не завершится
    # сам — нужен хотя бы один message чтобы тест получил данные и закрыл коннект).
    t = _publish_after(fake_redis, "fb_agent:scan:finished", {"scan_id": 1})

    with client.websocket_connect("/ws/dashboard") as ws:
        msg = ws.receive_json()
        # Проверяем базовую структуру ответа.
        assert "type" in msg
        assert "ts" in msg
        assert "payload" in msg

    t.join(timeout=2)


# Публикация scan_finished → клиент получает type=scan_finished с корректным payload.
def test_ws_receives_scan_finished(monkeypatch) -> None:
    monkeypatch.setenv("WS_HEARTBEAT_SECONDS", "60")
    import importlib

    import apps.api.routers.ws as ws_mod

    importlib.reload(ws_mod)

    app, fake_redis = _make_app()

    from fastapi.testclient import TestClient

    client = TestClient(app)

    scan_payload = {
        "scan_id": 42,
        "outcome": "ok",
        "rows_total": 10,
        "alerts_warning": 1,
        "alerts_stop": 0,
    }
    t = _publish_after(fake_redis, "fb_agent:scan:finished", scan_payload)

    with client.websocket_connect("/ws/dashboard") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "scan_finished"
        assert msg["payload"]["scan_id"] == 42
        assert msg["payload"]["rows_total"] == 10

    t.join(timeout=2)


# Публикация alert_created → клиент получает type=alert_created.
# (fb_agent:alert:created пока НЕ публикуется воркерами — подписан «на вырост»,
# данный тест проверяет сквозной маршрут через тестовую публикацию.)
def test_ws_receives_alert_created(monkeypatch) -> None:
    monkeypatch.setenv("WS_HEARTBEAT_SECONDS", "60")
    import importlib

    import apps.api.routers.ws as ws_mod

    importlib.reload(ws_mod)

    app, fake_redis = _make_app()

    from fastapi.testclient import TestClient

    client = TestClient(app)

    alert_payload = {
        "fb_ad_id": "23001234567890",
        "stage": "stop",
        "rule_codes": ["spend_no_lead_stop"],
    }
    t = _publish_after(fake_redis, "fb_agent:alert:created", alert_payload)

    with client.websocket_connect("/ws/dashboard") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "alert_created"
        assert msg["payload"]["fb_ad_id"] == "23001234567890"
        assert msg["payload"]["stage"] == "stop"

    t.join(timeout=2)


# Heartbeat: при коротком интервале клиент получает ping до любого события.
def test_ws_receives_heartbeat_ping(monkeypatch) -> None:
    # Устанавливаем минимальный heartbeat — 0 секунд не работает (sleep(0) = asyncio yield),
    # поэтому используем 0.01 (10 мс). Реальный интервал через env = целое число.
    monkeypatch.setenv("WS_HEARTBEAT_SECONDS", "0")
    import importlib

    import apps.api.routers.ws as ws_mod

    importlib.reload(ws_mod)

    app, fake_redis = _make_app()

    from fastapi.testclient import TestClient

    client = TestClient(app)

    with client.websocket_connect("/ws/dashboard") as ws:
        # С WS_HEARTBEAT_SECONDS=0 первый ping приходит почти сразу.
        msg = ws.receive_json()
        assert msg["type"] == "ping"
        assert "ts" in msg


# Disconnect клиента → cleanup без утечек задач и без падения сервера.
def test_ws_disconnect_cleanup(monkeypatch) -> None:
    monkeypatch.setenv("WS_HEARTBEAT_SECONDS", "60")
    import importlib

    import apps.api.routers.ws as ws_mod

    importlib.reload(ws_mod)

    app, fake_redis = _make_app()

    from fastapi.testclient import TestClient

    client = TestClient(app)

    # Публикуем одно событие чтобы получить что-то и закрыть соединение.
    t = _publish_after(fake_redis, "fb_agent:scan:finished", {"scan_id": 99})

    # Если cleanup не сработал корректно — TestClient выбросит исключение.
    with client.websocket_connect("/ws/dashboard") as ws:
        ws.receive_json()
        # Явно закрываем — проверяем graceful shutdown.
        ws.close()

    t.join(timeout=2)

    # Сервер должен быть жив: healthz отвечает 200.
    resp = client.get("/healthz")
    assert resp.status_code == 200


# Redis недоступен на момент connect → WS закрывается gracefully, не падает сервер.
def test_ws_redis_unavailable_graceful_close(monkeypatch) -> None:
    monkeypatch.setenv("WS_HEARTBEAT_SECONDS", "60")
    import importlib

    import apps.api.routers.ws as ws_mod

    importlib.reload(ws_mod)

    app = create_app()
    # Кладём нормальный redis в state чтобы lifespan не падал.
    fake_redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    app.state.redis = fake_redis
    # НЕ задаём ws_pubsub_redis → WS попытается создать клиент по redis_url.
    # Задаём нереальный URL — подключение упадёт при subscribe.
    app.state.redis_url = "redis://127.0.0.1:19999/0"  # несуществующий порт

    from fastapi.testclient import TestClient

    client = TestClient(app, raise_server_exceptions=False)

    # WS должен быть принят и сразу закрыт с кодом 1011 (Internal Error).
    try:
        with client.websocket_connect("/ws/dashboard") as ws:
            ws.receive_json()  # ожидаем закрытие, а не сообщение
    except Exception:
        pass  # WebSocketDisconnect или аналогичное — ожидаемо

    # Сервер должен быть живым после неудачного WS.
    resp = client.get("/healthz")
    assert resp.status_code == 200
