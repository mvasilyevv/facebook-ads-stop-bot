# -*- coding: utf-8 -*-
"""Unit-тесты для apps/health_watchdog/main.py — pure-функции."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from apps.health_watchdog.main import (
    DesyncedStopAd,
    StuckPauseTask,
    build_autostop_channel_alert,
    build_meta_channel_alert,
    check_observer_runtime_freshness,
    classify_meta_probe,
    is_login_required_reason,
    parse_expected_workers,
    should_alert,
)


# Базовый случай: CSV → нормализованный список без дубликатов и пустых
def test_parse_expected_workers_basic() -> None:
    assert parse_expected_workers("observer,disable,enable") == [
        "observer",
        "disable",
        "enable",
    ]


# Пробелы и пустые элементы отбрасываются, порядок сохраняется
def test_parse_expected_workers_strips_and_dedups() -> None:
    assert parse_expected_workers(" observer , , disable, observer , enable ") == [
        "observer",
        "disable",
        "enable",
    ]


# None и пустая строка дают пустой список
def test_parse_expected_workers_empty() -> None:
    assert parse_expected_workers(None) == []
    assert parse_expected_workers("") == []
    assert parse_expected_workers("   ") == []


# H4: money-критичные воркеры обязаны быть в дефолтном списке мониторинга
def test_default_expected_workers_covers_money_critical() -> None:
    from apps.health_watchdog.main import DEFAULT_EXPECTED_WORKERS

    workers = parse_expected_workers(DEFAULT_EXPECTED_WORKERS)
    for name in ("meta_api", "cabinet_scheduler", "tracker_aggregator", "observer"):
        assert name in workers, f"{name} должен мониториться (money-критичный)"
    # disable/enable удалены (DOM-канал) — их не должно быть в дефолте
    assert "disable" not in workers
    assert "enable" not in workers


# H4: дефолт watchdog согласован с health_details (UI и алертинг видят один набор;
# health_watchdog мониторит всё, КРОМЕ себя — если он сам мёртв, алертить некому)
def test_default_expected_workers_synced_with_health_details() -> None:
    from apps.api.routers.v1.health_details import _DEFAULT_EXPECTED_WORKERS
    from apps.health_watchdog.main import DEFAULT_EXPECTED_WORKERS

    watchdog = set(parse_expected_workers(DEFAULT_EXPECTED_WORKERS))
    details = set(_DEFAULT_EXPECTED_WORKERS)
    # health_details дополнительно содержит только health_watchdog (для UI-статуса)
    assert details - watchdog == {"health_watchdog"}, (
        f"рассинхрон watchdog↔health_details: {details ^ watchdog}"
    )


# ====================== classify_meta_probe (канал Marketing API) ======================


# probe успешен (healthy=True, probe_ok) → канал жив (is_down=False)
def test_classify_meta_probe_ok() -> None:
    probe = {"healthy": True, "probe_performed": True, "probe_ok": True, "probe_detail": "ok"}
    is_down, reason = classify_meta_probe(probe)
    assert is_down is False
    assert reason == "ok"


# КЛЮЧЕВОЙ кейс: token present, но fetch падает (Failed to fetch) → канал мёртв
def test_classify_meta_probe_network_down() -> None:
    probe = {
        "healthy": False,
        "probe_performed": True,
        "probe_ok": False,
        "detail": "probe_network_down",
        "probe_detail": "probe_network_down",
    }
    is_down, reason = classify_meta_probe(probe)
    assert is_down is True
    assert reason == "probe_network_down"


# Протухший токен (190) → канал мёртв для мутаций
def test_classify_meta_probe_token_invalid() -> None:
    probe = {
        "healthy": False,
        "probe_performed": True,
        "probe_ok": False,
        "probe_detail": "probe_token_invalid",
    }
    is_down, reason = classify_meta_probe(probe)
    assert is_down is True
    assert reason == "probe_token_invalid"


# Meta-side rate-limit: fetch дошёл до Meta → канал жив (не outage)
def test_classify_meta_probe_rate_limit_alive() -> None:
    probe = {
        "healthy": True,
        "probe_performed": True,
        "probe_ok": False,
        "probe_detail": "meta_error:17",
    }
    is_down, reason = classify_meta_probe(probe)
    assert is_down is False


# browser-agent недоступен (circuit_open, probe не выполнялся) → канал мёртв, причина из detail
def test_classify_meta_probe_circuit_open() -> None:
    probe = {
        "healthy": False,
        "probe_performed": False,
        "detail": "circuit_open: browser-agent недоступен",
        "probe_detail": "not_performed",
    }
    is_down, reason = classify_meta_probe(probe)
    assert is_down is True
    assert "circuit_open" in reason


# build_meta_channel_alert: money-сигнал + причина + CRITICAL-маркер
def test_build_meta_channel_alert_contains_money_signal() -> None:
    text = build_meta_channel_alert(reason="probe_network_down", detail="Failed to fetch")
    assert "probe_network_down" in text
    assert "Failed to fetch" in text
    # money-предупреждение и указание чинить канал
    assert "auto-stop" in text.lower() or "авто-стоп" in text.lower()


# MID X-16: probe вернул маркер login_required → канал мёртв, reason=login_required.
def test_classify_meta_probe_login_required() -> None:
    probe = {
        "healthy": False,
        "probe_performed": True,
        "probe_ok": False,
        "detail": "login_required",
        "probe_detail": "login_required",
    }
    is_down, reason = classify_meta_probe(probe)
    assert is_down is True
    assert reason == "login_required"


# MID X-16: is_login_required_reason различает разлогин от network-down/token-invalid.
def test_is_login_required_reason() -> None:
    assert is_login_required_reason("login_required") is True
    assert is_login_required_reason("LOGIN_REQUIRED") is True
    assert is_login_required_reason("probe_network_down") is False
    assert is_login_required_reason("probe_token_invalid") is False
    assert is_login_required_reason("meta_error:17") is False


# MID X-16: login_required → ОТДЕЛЬНЫЙ текст «нужен ре-логин Vision-профиля»,
# отличный от generic network-down (действие оператора — залогиниться, не рестарт канала).
def test_build_meta_channel_alert_login_required_distinct_text() -> None:
    text = build_meta_channel_alert(reason="login_required", detail="Session expired")
    low = text.lower()
    assert "ре-логин" in low or "re-login" in low
    assert "разлогин" in low or "залогин" in low
    # money-предупреждение сохраняется
    assert "авто-стоп" in low or "auto-stop" in low
    # generic-текст «канал Marketing API мёртв» НЕ используется для login_required
    assert "канал marketing api мёртв" not in low


# Нет ключа observer:runtime → stale=True с reason="missing"
def test_check_observer_runtime_freshness_missing() -> None:
    now = datetime(2026, 5, 27, 12, 0, 0, tzinfo=timezone.utc)
    is_stale, reason = check_observer_runtime_freshness(None, now=now)
    assert is_stale is True
    assert reason == "missing"


# Свежий updated_at (1 минута назад) → not stale
def test_check_observer_runtime_freshness_fresh() -> None:
    now = datetime(2026, 5, 27, 12, 0, 0, tzinfo=timezone.utc)
    updated = (now - timedelta(minutes=1)).isoformat()
    payload = json.dumps({"worker_status": "scanning", "updated_at": updated})
    is_stale, reason = check_observer_runtime_freshness(payload, now=now)
    assert is_stale is False
    assert reason is None


# updated_at старше 5 минут → stale с информативным reason
def test_check_observer_runtime_freshness_stale() -> None:
    now = datetime(2026, 5, 27, 12, 0, 0, tzinfo=timezone.utc)
    updated = (now - timedelta(minutes=15)).isoformat()
    payload = json.dumps({"worker_status": "idle", "updated_at": updated})
    is_stale, reason = check_observer_runtime_freshness(payload, now=now)
    assert is_stale is True
    assert reason is not None
    assert "15" in reason


# Невалидный JSON → stale с reason="invalid_json"
def test_check_observer_runtime_freshness_invalid_json() -> None:
    now = datetime(2026, 5, 27, 12, 0, 0, tzinfo=timezone.utc)
    is_stale, reason = check_observer_runtime_freshness("{not valid", now=now)
    assert is_stale is True
    assert reason == "invalid_json"


# Нет updated_at в JSON → stale
def test_check_observer_runtime_freshness_no_updated_at() -> None:
    now = datetime(2026, 5, 27, 12, 0, 0, tzinfo=timezone.utc)
    payload = json.dumps({"worker_status": "scanning"})
    is_stale, reason = check_observer_runtime_freshness(payload, now=now)
    assert is_stale is True
    assert reason == "missing_updated_at"


# naive datetime в payload должен интерпретироваться как UTC
def test_check_observer_runtime_freshness_naive_datetime_is_utc() -> None:
    now = datetime(2026, 5, 27, 12, 0, 0, tzinfo=timezone.utc)
    naive = (now - timedelta(minutes=1)).replace(tzinfo=None).isoformat()
    payload = json.dumps({"worker_status": "scanning", "updated_at": naive})
    is_stale, reason = check_observer_runtime_freshness(payload, now=now)
    assert is_stale is False
    assert reason is None


# Кастомный max_age_seconds — короче дефолта
def test_check_observer_runtime_freshness_custom_max_age() -> None:
    now = datetime(2026, 5, 27, 12, 0, 0, tzinfo=timezone.utc)
    updated = (now - timedelta(seconds=90)).isoformat()
    payload = json.dumps({"worker_status": "scanning", "updated_at": updated})
    is_stale, _ = check_observer_runtime_freshness(payload, now=now, max_age_seconds=60)
    assert is_stale is True


# heartbeat есть → не алертим
def test_should_alert_heartbeat_alive() -> None:
    assert should_alert("alive", None) is False


# heartbeat истёк + дедуп уже стоит → не алертим повторно
def test_should_alert_dedup_active() -> None:
    assert should_alert(None, "1") is False


# heartbeat истёк + дедупа нет → алертим
def test_should_alert_heartbeat_dead_and_no_dedup() -> None:
    assert should_alert(None, None) is True


# ====================== канал авто-стопа: build_autostop_channel_alert ======================


# Оба триггера пусты (канал жив) → алерта нет
def test_autostop_alert_no_triggers_returns_none() -> None:
    assert build_autostop_channel_alert([], []) is None


# Только застрявшие задачи pause_ad → алерт с count, target_id, попытками и last_error
def test_autostop_alert_stuck_tasks_only() -> None:
    tasks = [
        StuckPauseTask(
            task_id=101,
            target_id="23001",
            attempt_count=16,
            age_minutes=42,
            last_error="Failed to fetch",
        )
    ]
    text = build_autostop_channel_alert(tasks, [])
    assert text is not None
    # Money-критичный CRITICAL-маркер и контекст канала
    assert "авто-стоп" in text.lower()
    assert "23001" in text  # target_id (fb_ad_id) пострадавшего объявления
    assert "16" in text  # число попыток
    assert "42" in text  # возраст застревания в минутах
    assert "Failed to fetch" in text  # диагностика (триггер 1 как обогащение)


# Только рассинхрон (stop_sent при ACTIVE) → алерт со списком fb_ad_id
def test_autostop_alert_desync_only() -> None:
    desynced = [DesyncedStopAd(fb_ad_id="987654", age_minutes=30)]
    text = build_autostop_channel_alert([], desynced)
    assert text is not None
    assert "987654" in text
    assert "30" in text
    # Рассинхрон-симптом: объявление в stop_sent, но крутится (ACTIVE)
    assert "stop_sent" in text or "ACTIVE" in text


# Оба триггера сразу → в тексте оба раздела
def test_autostop_alert_both_triggers() -> None:
    tasks = [
        StuckPauseTask(task_id=1, target_id="111", attempt_count=5, age_minutes=20, last_error=None)
    ]
    desynced = [DesyncedStopAd(fb_ad_id="222", age_minutes=25)]
    text = build_autostop_channel_alert(tasks, desynced)
    assert text is not None
    assert "111" in text
    assert "222" in text


# last_error=None не должен ронять рендер (например задача ещё ни разу не падала)
def test_autostop_alert_stuck_task_without_error() -> None:
    tasks = [
        StuckPauseTask(task_id=1, target_id="111", attempt_count=0, age_minutes=20, last_error=None)
    ]
    text = build_autostop_channel_alert(tasks, [])
    assert text is not None
    assert "111" in text


# Длинные списки усекаются (не раздуваем TG-сообщение), с пометкой «ещё N»
def test_autostop_alert_truncates_long_lists() -> None:
    tasks = [
        StuckPauseTask(
            task_id=i, target_id=f"ad{i}", attempt_count=3, age_minutes=20, last_error="x"
        )
        for i in range(25)
    ]
    text = build_autostop_channel_alert(tasks, [])
    assert text is not None
    # Общее число должно фигурировать целиком
    assert "25" in text
    # Но не все 25 строк перечислены поимённо — есть пометка про остаток
    assert "ещё" in text.lower()
    # Telegram-лимит сообщения 4096 — не превышаем
    assert len(text) < 4096


# Сканирование выключено → канал авто-стопа НЕ проверяется и CRITICAL не шлётся
# (browser-agent намеренно не держит сессию; «сессия не найдена» — ожидаемо, не отказ).
async def test_check_meta_api_channel_skips_when_scanning_off(monkeypatch) -> None:
    import apps.health_watchdog.main as hw
    from core.observer import queries as obs_queries

    async def _fake_config(engine):
        return {"is_scanning_enabled": False}

    monkeypatch.setattr(obs_queries, "load_observer_config", _fake_config)

    probe_called = {"v": False}

    class _Client:
        async def check_health(self, *, full_probe: bool = False):
            probe_called["v"] = True
            return {"healthy": False}

    class _Redis:
        def __init__(self):
            self.store: dict[str, str] = {}
            self.deleted: list[str] = []

        async def set(self, key, value, ex=None):
            self.store[key] = value
            return True

        async def delete(self, key):
            self.deleted.append(key)
            return 1

    redis = _Redis()
    sent = await hw.check_meta_api_channel(
        _Client(), redis, engine=None, now=datetime(2026, 1, 1, tzinfo=timezone.utc)
    )
    assert sent is False  # CRITICAL не отправлен
    assert probe_called["v"] is False  # check_health даже не звался (сканирование off)
    payload = json.loads(redis.store[hw.META_CHANNEL_HEALTH_KEY])
    assert payload["probe_detail"] == "scanning_disabled"
    assert payload["healthy"] is None  # skipped probe != подтверждённый отказ
    assert hw.META_CHANNEL_DEDUP_KEY in redis.deleted  # дедуп снят (re-arm на будущее)


# Сканирование ВКЛЮЧЕНО + канал реально мёртв → CRITICAL отправляется (регрессия фикса).
async def test_check_meta_api_channel_alerts_when_scanning_on_and_down(monkeypatch) -> None:
    import apps.health_watchdog.main as hw
    from core.observer import queries as obs_queries

    async def _fake_config(engine):
        return {"is_scanning_enabled": True}

    monkeypatch.setattr(obs_queries, "load_observer_config", _fake_config)

    class _Client:
        async def check_health(self, *, full_probe: bool = False):
            return {"healthy": False, "detail": "network-down"}

    class _Redis:
        def __init__(self):
            self.store: dict[str, str] = {}

        async def set(self, key, value, ex=None, nx=False):
            if nx and key in self.store:
                return None
            self.store[key] = value
            return True

        async def delete(self, key):
            self.store.pop(key, None)
            return 1

    async def _fake_alert(redis_client, *, dedup_key, text, engine):
        return True  # имитируем «алерт отправлен»

    monkeypatch.setattr(hw, "_maybe_alert_with_dedup", _fake_alert)

    redis = _Redis()
    sent = await hw.check_meta_api_channel(
        _Client(), redis, engine=None, now=datetime(2026, 1, 1, tzinfo=timezone.utc)
    )
    assert sent is True  # при включённом сканировании реальный отказ → CRITICAL


# ====================== сторожок «тени отчётности Meta»: check_shadow_spend ======================


class _ShadowRedis:
    """Fake Redis: строки (get/set/delete) + список снимков (lpush/lrange/ltrim/expire)."""

    def __init__(self, seed: dict[str, str] | None = None) -> None:
        self.store: dict[str, str] = dict(seed or {})
        self.lists: dict[str, list[str]] = {}
        self.deleted: list[str] = []
        self.published: list[tuple[str, str]] = []

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def delete(self, key):
        self.deleted.append(key)
        self.store.pop(key, None)
        self.lists.pop(key, None)
        return 1

    async def lpush(self, key, value):
        self.lists.setdefault(key, []).insert(0, value)
        return len(self.lists[key])

    async def lrange(self, key, start, end):
        items = self.lists.get(key, [])
        return items[start : end + 1]

    async def ltrim(self, key, start, end):
        if key in self.lists:
            self.lists[key] = self.lists[key][start : end + 1]
        return True

    async def expire(self, key, ttl):
        return True

    async def publish(self, channel, payload):
        self.published.append((channel, payload))
        return 1


class _ShadowMetaClient:
    """Fake MetaApiClient: execute_graph_call отдаёт amount_spent, флаг вызванности."""

    def __init__(self, amount_spent: str = "1030") -> None:
        self.amount_spent = amount_spent
        self.called = False

    async def execute_graph_call(self, **kwargs):
        self.called = True
        return {"amount_spent": self.amount_spent}


def _fresh_runtime(now: datetime) -> str:
    """JSON observer:runtime со свежим updated_at (гейт сторожка проходит)."""
    return json.dumps({"worker_status": "scanning", "updated_at": now.isoformat()})


# Гейт: сканирование выключено → Graph-вызов НЕ делается, алерта нет
async def test_check_shadow_spend_skips_when_scanning_off(monkeypatch) -> None:
    import apps.health_watchdog.main as hw
    from core.observer import queries as obs_queries

    async def _fake_config(engine):
        return {"is_scanning_enabled": False}

    monkeypatch.setattr(obs_queries, "load_observer_config", _fake_config)

    now = datetime(2026, 7, 3, 8, 40, tzinfo=timezone.utc)
    redis = _ShadowRedis(seed={hw.OBSERVER_RUNTIME_KEY: _fresh_runtime(now)})
    client = _ShadowMetaClient()

    sent = await hw.check_shadow_spend(client, redis, engine=None, now=now)

    assert sent is False
    assert client.called is False  # биллинг даже не запрашивался (гейт закрыт)


# Гейт: сканирование включено, но observer:runtime устарел → Graph не зовётся (отчётность стоит по определению)
async def test_check_shadow_spend_skips_when_observer_stale(monkeypatch) -> None:
    import apps.health_watchdog.main as hw
    from core.observer import queries as obs_queries

    async def _fake_config(engine):
        return {"is_scanning_enabled": True}

    monkeypatch.setattr(obs_queries, "load_observer_config", _fake_config)

    now = datetime(2026, 7, 3, 8, 40, tzinfo=timezone.utc)
    stale = json.dumps(
        {"worker_status": "idle", "updated_at": (now - timedelta(minutes=20)).isoformat()}
    )
    redis = _ShadowRedis(seed={hw.OBSERVER_RUNTIME_KEY: stale})
    client = _ShadowMetaClient()

    sent = await hw.check_shadow_spend(client, redis, engine=None, now=now)

    assert sent is False
    assert client.called is False


# Вердикт: биллинг растёт (+30¢), отчётность стоит → notify + дедуп ставится
async def test_check_shadow_spend_alerts_on_verdict(monkeypatch) -> None:
    import apps.health_watchdog.main as hw
    from core.dashboard import cabinet_spend
    from core.meta_api import account_tz
    from core.observer import queries as obs_queries

    async def _fake_config(engine):
        return {"is_scanning_enabled": True}

    async def _fake_accounts(engine):
        return ["111222"]

    async def _fake_offset_map(redis, account_ids, **kwargs):
        return {"111222": 0.0}

    # Пер-адная отчётность стоит на $5.00 (500¢) оба тика.
    async def _fake_reported(engine, *, tz_map, default_offset, now):
        from decimal import Decimal

        return Decimal("5.00")

    monkeypatch.setattr(obs_queries, "load_observer_config", _fake_config)
    monkeypatch.setattr(account_tz, "active_account_ids", _fake_accounts)
    monkeypatch.setattr(account_tz, "load_offset_map", _fake_offset_map)
    monkeypatch.setattr(cabinet_spend, "current_day_spend", _fake_reported)

    captured: dict[str, object] = {}

    async def _fake_alert(redis_client, *, dedup_key, text, engine, ttl_seconds):
        captured["dedup_key"] = dedup_key
        captured["text"] = text
        captured["ttl"] = ttl_seconds
        return True

    monkeypatch.setattr(hw, "_maybe_alert_with_dedup", _fake_alert)

    t0 = datetime(2026, 7, 3, 8, 40, tzinfo=timezone.utc)
    redis = _ShadowRedis(seed={hw.OBSERVER_RUNTIME_KEY: _fresh_runtime(t0)})

    # Тик 1: биллинг 1000¢ — сэмпл записан, пары ещё нет → алерта нет.
    client1 = _ShadowMetaClient(amount_spent="1000")
    sent1 = await hw.check_shadow_spend(client1, redis, engine=None, now=t0)
    assert sent1 is False
    assert "dedup_key" not in captured

    # Тик 2 (через 5 мин): биллинг 1030¢ (+30¢), отчётность стоит → тревога.
    t1 = t0 + timedelta(seconds=300)
    redis.store[hw.OBSERVER_RUNTIME_KEY] = _fresh_runtime(t1)
    client2 = _ShadowMetaClient(amount_spent="1030")
    sent2 = await hw.check_shadow_spend(client2, redis, engine=None, now=t1)

    assert sent2 is True
    assert captured["dedup_key"] == f"{hw.SHADOW_DEDUP_PREFIX}111222"
    assert captured["ttl"] == hw.SHADOW_ALERT_DEDUP_TTL_SECONDS
    # Money-контекст в тексте: сумма прироста и «отчётность стоит».
    assert "0.30" in captured["text"]
    assert "act_111222" in captured["text"]


# Недоставка алерта (notify вернул False) → дедуп НЕ ставится (re-arm на следующий тик)
async def test_check_shadow_spend_rearms_on_undelivered(monkeypatch) -> None:
    import apps.health_watchdog.main as hw
    from core.dashboard import cabinet_spend
    from core.meta_api import account_tz
    from core.observer import queries as obs_queries

    async def _fake_config(engine):
        return {"is_scanning_enabled": True}

    async def _fake_accounts(engine):
        return ["111222"]

    async def _fake_offset_map(redis, account_ids, **kwargs):
        return {"111222": 0.0}

    async def _fake_reported(engine, *, tz_map, default_offset, now):
        from decimal import Decimal

        return Decimal("5.00")

    monkeypatch.setattr(obs_queries, "load_observer_config", _fake_config)
    monkeypatch.setattr(account_tz, "active_account_ids", _fake_accounts)
    monkeypatch.setattr(account_tz, "load_offset_map", _fake_offset_map)
    monkeypatch.setattr(cabinet_spend, "current_day_spend", _fake_reported)

    # Реальный _maybe_alert_with_dedup, но notify_recipients падает в «не доставлено».
    async def _fake_notify(engine, redis, *, category, text):
        return False

    monkeypatch.setattr(hw, "notify_recipients", _fake_notify)

    t0 = datetime(2026, 7, 3, 8, 40, tzinfo=timezone.utc)
    redis = _ShadowRedis(seed={hw.OBSERVER_RUNTIME_KEY: _fresh_runtime(t0)})

    client1 = _ShadowMetaClient(amount_spent="1000")
    await hw.check_shadow_spend(client1, redis, engine=None, now=t0)

    t1 = t0 + timedelta(seconds=300)
    redis.store[hw.OBSERVER_RUNTIME_KEY] = _fresh_runtime(t1)
    client2 = _ShadowMetaClient(amount_spent="1030")
    sent = await hw.check_shadow_spend(client2, redis, engine=None, now=t1)

    assert sent is False  # не доставлено
    # Дедуп-ключ НЕ выставлен → следующий тик повторит попытку (алерт не потерян).
    assert f"{hw.SHADOW_DEDUP_PREFIX}111222" not in redis.store
    # Web-critical живёт независимо от Telegram recipients.
    assert f"{hw.SHADOW_CRITICAL_KEY_PREFIX}111222" in redis.store


async def test_shadow_billing_tick_triggers_fast_observer_before_critical(monkeypatch) -> None:
    """Даже +1¢ при стоящей per-ad стороне будит observer; CRITICAL ждёт порога 25¢."""
    import apps.health_watchdog.main as hw
    from core.dashboard import cabinet_spend
    from core.meta_api import account_tz
    from core.observer import queries as obs_queries

    async def _fake_config(engine):
        return {"is_scanning_enabled": True}

    async def _fake_accounts(engine):
        return ["111222"]

    async def _fake_offset_map(redis, account_ids, **kwargs):
        return {"111222": 0.0}

    async def _fake_reported(engine, *, tz_map, default_offset, now):
        from decimal import Decimal

        return Decimal("5.00")

    monkeypatch.setattr(obs_queries, "load_observer_config", _fake_config)
    monkeypatch.setattr(account_tz, "active_account_ids", _fake_accounts)
    monkeypatch.setattr(account_tz, "load_offset_map", _fake_offset_map)
    monkeypatch.setattr(cabinet_spend, "current_day_spend", _fake_reported)

    t0 = datetime(2026, 7, 3, 8, 40, tzinfo=timezone.utc)
    redis = _ShadowRedis(seed={hw.OBSERVER_RUNTIME_KEY: _fresh_runtime(t0)})
    await hw.check_shadow_spend(_ShadowMetaClient("1000"), redis, engine=None, now=t0)

    t1 = t0 + timedelta(seconds=30)
    redis.store[hw.OBSERVER_RUNTIME_KEY] = _fresh_runtime(t1)
    sent = await hw.check_shadow_spend(_ShadowMetaClient("1001"), redis, engine=None, now=t1)

    assert sent is False
    burst = json.loads(redis.store[hw.SHADOW_BURST_KEY])
    assert burst["account_id"] == "111222"
    assert any(channel == "fb_agent:observer:trigger" for channel, _ in redis.published)
    assert f"{hw.SHADOW_CRITICAL_KEY_PREFIX}111222" not in redis.store
