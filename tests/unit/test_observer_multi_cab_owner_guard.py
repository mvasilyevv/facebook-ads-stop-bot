# -*- coding: utf-8 -*-
"""Юнит-тест money-гарда R4: мульти-каб (>1 кабинета) без owner_tag = скан остановлен.

Зеркало single-cab guard allowlist_blocks_scan: в shared-кабинете без owner_tag
campaign_matches_owner→True для ВСЕХ → бот авто-стопнул бы чужую рекламу (необратимо).
"""

from __future__ import annotations

import json

from apps.observer_worker import main as observer_main
from core.observer.queries import multi_cabinet_requires_owner_tag


class _FakeRedis:
    """Fake redis: SET NX EX + observer:runtime payload (как в prepare_workspace тестах)."""

    def __init__(self):
        self.store: dict[str, str] = {}

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def get(self, key):
        return self.store.get(key)


class _ExplodingGate:
    """Gate, который ДОЛЖЕН остаться нетронутым: любой вызов = провал гарда."""

    async def run_one_scan(self, **kwargs):
        raise AssertionError("run_one_scan не должен вызываться при сработавшем гарде")

    async def open_cabinet_tabs(self, ad_account_ids):
        raise AssertionError("open_cabinet_tabs не должен вызываться при сработавшем гарде")


# Мульти-каб (>1) + пустой owner_tag → скан надо ПРОПУСТИТЬ (guard=True)
def test_multi_cab_empty_tag_blocks() -> None:
    assert multi_cabinet_requires_owner_tag(2, None) is True
    assert multi_cabinet_requires_owner_tag(2, "") is True
    assert multi_cabinet_requires_owner_tag(2, "   ") is True
    assert multi_cabinet_requires_owner_tag(5, ",;") is True  # только разделители = пусто


# Мульти-каб (>1) + заданный owner_tag → скан безопасен (guard=False)
def test_multi_cab_with_tag_allows() -> None:
    assert multi_cabinet_requires_owner_tag(2, "MV") is False
    assert multi_cabinet_requires_owner_tag(3, "MV,ABC") is False


# Один кабинет (или 0) — guard неприменим (там работает allowlist_blocks_scan)
def test_single_or_zero_cab_never_blocks() -> None:
    assert multi_cabinet_requires_owner_tag(1, None) is False
    assert multi_cabinet_requires_owner_tag(1, "") is False
    assert multi_cabinet_requires_owner_tag(0, None) is False
    assert multi_cabinet_requires_owner_tag(1, "MV") is False


def _patch_cycle_deps(monkeypatch, *, owner_tag, accounts):
    """Подменяет I/O-зависимости run_one_cycle на in-memory фейки (без БД)."""

    async def _cfg(_engine):
        return {
            "is_scanning_enabled": True,
            "owner_campaign_tag": owner_tag,
            "campaign_ids": [],
            "interval_seconds": 90,
        }

    async def _auto_restart(_engine):
        return True

    async def _accounts(_engine):
        return list(accounts)

    async def _orphans(_engine):
        return []

    async def _fail_process(*args, **kwargs):
        raise AssertionError("process_scan_rows не должен вызываться при сработавшем гарде")

    monkeypatch.setattr(observer_main, "load_observer_config", _cfg)
    monkeypatch.setattr(observer_main, "load_vision_auto_restart_flag", _auto_restart)
    monkeypatch.setattr(observer_main, "resolve_scan_account_ids", _accounts)
    monkeypatch.setattr(observer_main, "list_offers_without_accounts", _orphans)
    monkeypatch.setattr(observer_main, "process_scan_rows", _fail_process)


# Мульти-каб + пустой owner_tag: run_one_cycle ПРОПУСКАЕТ скан (gate/process не дёргаются),
# пишет skipped-исход и шлёт deduped ops-алерт. Это money-гард против авто-стопа чужой рекламы.
async def test_run_one_cycle_skips_multi_cab_without_owner_tag(monkeypatch) -> None:
    _patch_cycle_deps(monkeypatch, owner_tag=None, accounts=["111", "222"])
    redis = _FakeRedis()
    summary = await observer_main.run_one_cycle(
        engine=None, gate=_ExplodingGate(), redis_client=redis, tg_client=None
    )
    assert summary["outcome"] == "skipped"
    assert summary["reason"] == "multi_cab_no_owner_tag"
    # Дедуп-ключ ops-алерта выставлен.
    assert observer_main.MULTI_CAB_NO_OWNER_ALERT_DEDUP_KEY in redis.store
    # Runtime отражает остановку по безопасности (не paused — скан включён, но небезопасен).
    payload = json.loads(redis.store["observer:runtime"])
    assert payload["status"] == "running"
    assert "owner_tag" in payload["status_message"]


# Мульти-каб С owner_tag: гард НЕ срабатывает — НЕ возвращаем skipped (доходим до скана).
# Скан стопаем на пустых строках (process_scan_rows не зовётся), engine не нужен:
# ставим gate, который сразу бросает — раз skipped не вернули, значит дошли до scan-фазы.
async def test_run_one_cycle_proceeds_multi_cab_with_owner_tag(monkeypatch) -> None:
    _patch_cycle_deps(monkeypatch, owner_tag="MV", accounts=["111", "222"])
    redis = _FakeRedis()

    reached_scan = {"value": False}

    class _Gate:
        async def run_one_scan(self, **kwargs):
            reached_scan["value"] = True
            raise RuntimeError("stop-here: гард пропустил, дошли до скана")

        async def open_cabinet_tabs(self, ad_account_ids):
            return [{"ad_account_id": a, "opened": True} for a in ad_account_ids]

    observer_main._reset_prepared_accounts()
    # _run_account_scan ловит исключение скана и пишет в scan_runs (нужен engine) — но до
    # этого выставит reached_scan. Перехватываем падение _begin_scan_run/_finish_scan_run.
    monkeypatch.setattr(observer_main, "_begin_scan_run", _async_ret(1))
    monkeypatch.setattr(observer_main, "_finish_scan_run", _async_ret(None))
    monkeypatch.setattr(observer_main, "_publish_scan_finished", _async_ret(None))
    summary = await observer_main.run_one_cycle(
        engine=None, gate=_Gate(), redis_client=redis, tg_client=None
    )
    observer_main._reset_prepared_accounts()
    assert reached_scan["value"] is True  # гард не сработал, дошли до скан-фазы
    assert summary["outcome"] != "skipped"
    assert observer_main.MULTI_CAB_NO_OWNER_ALERT_DEDUP_KEY not in redis.store


def _async_ret(value):
    async def _f(*args, **kwargs):
        return value

    return _f
