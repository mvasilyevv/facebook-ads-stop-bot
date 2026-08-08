# -*- coding: utf-8 -*-
"""Unit-тесты pure-хелперов автостарта кабинета (core/scheduler/cabinet_autostart.py)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from core.meta_api.bulk import AccountScopedAdResolution, AutostartActivationGuards
from core.scheduler.cabinet_autostart import (
    DEFAULT_CONFIG,
    _normalize_config,
    is_in_autostart_window,
)


# Окно открывается ровно в HH:MM — это «в окне»
def test_is_in_window_at_boundary() -> None:
    now = datetime(2026, 5, 29, 6, 0, 0, tzinfo=timezone.utc)
    assert is_in_autostart_window(now, 6, 0) is True


# Сразу после планового времени — окно открыто (catch-up до конца суток)
def test_is_in_window_after_target() -> None:
    now = datetime(2026, 5, 29, 9, 30, 0, tzinfo=timezone.utc)
    assert is_in_autostart_window(now, 6, 0) is True


# 23:59 UTC — последняя минута суток, окно ещё открыто (catch-up)
def test_is_in_window_until_midnight() -> None:
    now = datetime(2026, 5, 29, 23, 59, 0, tzinfo=timezone.utc)
    assert is_in_autostart_window(now, 6, 0) is True


# Раньше планового времени — НЕ в окне
def test_is_in_window_before_target() -> None:
    now = datetime(2026, 5, 29, 5, 59, 0, tzinfo=timezone.utc)
    assert is_in_autostart_window(now, 6, 0) is False


# Окно с минутами: 06:30 — на 06:29 ещё рано, на 06:30 уже в окне
def test_is_in_window_with_minutes() -> None:
    assert is_in_autostart_window(datetime(2026, 5, 29, 6, 29, tzinfo=timezone.utc), 6, 30) is False
    assert is_in_autostart_window(datetime(2026, 5, 29, 6, 30, tzinfo=timezone.utc), 6, 30) is True


# Не-UTC tz приводится к UTC: 09:00 +3 == 06:00 UTC → в окне для 06:00
def test_is_in_window_converts_tz() -> None:
    plus3 = timezone(timedelta(hours=3))
    now = datetime(2026, 5, 29, 9, 0, 0, tzinfo=plus3)
    assert is_in_autostart_window(now, 6, 0) is True


# Naive datetime запрещён — функция требует timezone-aware
def test_is_in_window_rejects_naive() -> None:
    with pytest.raises(ValueError):
        is_in_autostart_window(datetime(2026, 5, 29, 6, 0, 0), 6, 0)


# Пустой/None конфиг нормализуется в дефолты (фича выключена)
def test_normalize_empty_returns_defaults() -> None:
    cfg = _normalize_config(None)
    assert cfg["enabled"] is False
    assert cfg["hour_utc"] == DEFAULT_CONFIG["hour_utc"]
    assert cfg["minute_utc"] == DEFAULT_CONFIG["minute_utc"]


# Нормализация приводит типы (enabled/час/минута); кампании в конфиге не хранятся
def test_normalize_coerces_types() -> None:
    cfg = _normalize_config({"enabled": 1, "hour_utc": "7", "minute_utc": "15"})
    assert cfg["enabled"] is True
    assert cfg["hour_utc"] == 7
    assert cfg["minute_utc"] == 15
    assert "campaign_ids" not in cfg, "campaign_ids живёт в observer allowlist, не здесь"


def _patch_window_open(monkeypatch, m, *, campaign_ids, owner_tag="MV"):
    from unittest.mock import AsyncMock

    monkeypatch.setattr(m, "load_scanning_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(
        m,
        "read_autostart_config",
        AsyncMock(return_value={"enabled": True, "hour_utc": 6, "minute_utc": 0}),
    )
    monkeypatch.setattr(
        m,
        "load_observer_config",
        AsyncMock(return_value={"owner_campaign_tag": owner_tag, "campaign_ids": campaign_ids}),
    )
    monkeypatch.setattr(m, "_load_scheduled_autostart_ads", AsyncMock(return_value=([], set())))
    monkeypatch.setattr(
        m,
        "capture_autostart_activation_guards",
        AsyncMock(
            side_effect=lambda _connection, *, ad_ids, **_kwargs: AutostartActivationGuards(
                guards_by_ad_id={
                    ad_id: {"version": 1, "generation": f"generation:{ad_id}"} for ad_id in ad_ids
                },
                rejected_by_ad_id={},
            )
        ),
    )
    monkeypatch.setattr(
        m,
        "enqueue_observer_scan",
        AsyncMock(return_value=SimpleNamespace(task_id=900, created=True)),
    )
    monkeypatch.setattr(m, "notify_owners_in_transaction", AsyncMock(return_value=True))


class _UnitConnection:
    async def execute(self, *_args, **_kwargs):
        return None


class _UnitBegin:
    async def __aenter__(self):
        return _UnitConnection()

    async def __aexit__(self, *_args):
        return None


class _UnitEngine:
    def begin(self):
        return _UnitBegin()


# M3: >MAX_BULK объявлений → несколько bulk-задач (чанки ≤50) с уникальными idem-ключами,
# объединение чанков = все объявления (раньше включались только первые 50, остаток терялся).
@pytest.mark.asyncio
async def test_started_chunks_over_max_bulk(monkeypatch) -> None:
    from unittest.mock import AsyncMock

    import apps.cabinet_scheduler.main as m

    _patch_window_open(monkeypatch, m, campaign_ids=["c1"])
    ad_ids = [f"ad{i:03d}" for i in range(120)]  # 120 → чанки 50/50/20
    monkeypatch.setattr(
        m,
        "resolve_owner_ads_by_account",
        AsyncMock(
            return_value=AccountScopedAdResolution(
                ads_by_account={"123": tuple(ad_ids)}, total=120, missing_account_count=0
            )
        ),
    )
    created: list = []

    async def _fake_create(
        engine,
        *,
        payload,
        requested_by,
        status,
        idempotency_key,
        connection,
    ):
        created.append((payload, idempotency_key))
        return len(created)

    monkeypatch.setattr(m, "create_mutation_task", _fake_create)
    now = datetime(2026, 5, 29, 9, 0, 0, tzinfo=timezone.utc)

    summary = await m.run_one_tick(engine=_UnitEngine(), now=now)

    assert summary["outcome"] == "started"
    assert summary["ad_count"] == 120
    assert summary["chunks"] == 3
    assert len(summary["task_ids"]) == 3
    assert summary["truncated"] is False
    all_ids: list = []
    keys = set()
    for payload, key in created:
        assert payload.ad_account_id == "123"
        assert len(payload.params["ad_ids"]) <= m.MAX_BULK
        all_ids.extend(payload.params["ad_ids"])
        keys.add(key)
    assert sorted(all_ids) == sorted(ad_ids), "все объявления должны попасть в задачи"
    assert len(keys) == 3, "idempotency_key каждого чанка уникален"


# M5: при no_owner_ads один и тот же durable scan key не дублирует работу.
@pytest.mark.asyncio
async def test_no_owner_ads_scan_triggers_once_per_day(monkeypatch) -> None:
    from unittest.mock import AsyncMock

    import apps.cabinet_scheduler.main as m

    _patch_window_open(monkeypatch, m, campaign_ids=["c1"])
    monkeypatch.setattr(
        m,
        "resolve_owner_ads_by_account",
        AsyncMock(return_value=AccountScopedAdResolution({}, 0, 0)),
    )
    enqueue = AsyncMock(
        side_effect=[
            SimpleNamespace(task_id=901, created=True),
            SimpleNamespace(task_id=901, created=False),
        ]
    )
    monkeypatch.setattr(m, "enqueue_observer_scan", enqueue)
    now = datetime(2026, 5, 29, 9, 0, 0, tzinfo=timezone.utc)

    first = await m.run_one_tick(engine=_UnitEngine(), now=now)
    second = await m.run_one_tick(engine=_UnitEngine(), now=now)

    assert first["outcome"] == "no_owner_ads" and first["scan_triggered"] is True
    assert second["outcome"] == "no_owner_ads" and second["scan_triggered"] is False
    assert enqueue.await_count == 2
    assert first["scan_task_id"] == second["scan_task_id"] == 901


@pytest.mark.asyncio
async def test_started_groups_tasks_by_explicit_account(monkeypatch) -> None:
    from unittest.mock import AsyncMock

    import apps.cabinet_scheduler.main as m

    _patch_window_open(monkeypatch, m, campaign_ids=["c1", "c2"])
    monkeypatch.setattr(
        m,
        "resolve_owner_ads_by_account",
        AsyncMock(
            return_value=AccountScopedAdResolution(
                ads_by_account={"222": ("ad2",), "111": ("ad1", "ad3")},
                total=3,
                missing_account_count=0,
            )
        ),
    )
    created = []

    async def _fake_create(
        engine,
        *,
        payload,
        requested_by,
        status,
        idempotency_key,
        connection,
    ):
        created.append((payload, idempotency_key))
        return len(created)

    monkeypatch.setattr(m, "create_mutation_task", _fake_create)
    summary = await m.run_one_tick(
        engine=_UnitEngine(),
        now=datetime(2026, 5, 29, 9, tzinfo=timezone.utc),
    )

    assert summary["outcome"] == "started"
    assert summary["accounts"] == 2
    assert [(p.ad_account_id, p.params["ad_ids"]) for p, _ in created] == [
        ("111", ["ad1", "ad3"]),
        ("222", ["ad2"]),
    ]


@pytest.mark.asyncio
async def test_missing_account_rejects_entire_run_without_enqueue(monkeypatch) -> None:
    from unittest.mock import AsyncMock

    import apps.cabinet_scheduler.main as m

    _patch_window_open(monkeypatch, m, campaign_ids=["c1"])
    monkeypatch.setattr(
        m,
        "resolve_owner_ads_by_account",
        AsyncMock(
            return_value=AccountScopedAdResolution(
                ads_by_account={"123": ("ad1",)}, total=2, missing_account_count=1
            )
        ),
    )
    create = AsyncMock()
    monkeypatch.setattr(m, "create_mutation_task", create)
    summary = await m.run_one_tick(
        engine=_UnitEngine(),
        now=datetime(2026, 5, 29, 9, tzinfo=timezone.utc),
    )

    assert summary["outcome"] == "rejected_missing_account"
    assert summary["task_ids"] == []
    create.assert_not_awaited()
