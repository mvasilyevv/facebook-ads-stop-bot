"""Тесты фазы «подготовка рабочего места» observer.

Перед сканом observer открывает вкладки Ads Manager кабинетов активных офферов
(manage/campaigns + колонки), пишет статус preparing и шлёт TG. Идемпотентно по
набору кабинетов: повтор того же набора — пропуск, смена набора — переподготовка.
"""

import json

import pytest

from apps.observer_worker import main as observer_main


class _FakeGate:
    """Fake ScannerGate: фиксирует вызовы open_cabinet_tabs, возвращает заданное."""

    def __init__(self, results_factory=None):
        self.calls: list[list[str]] = []
        self._results_factory = results_factory

    async def run_one_scan(self, **kwargs):  # в этих тестах не используется
        from apps.observer_worker.main import ScanCycleOutput

        return ScanCycleOutput(rows=[])

    async def open_cabinet_tabs(self, ad_account_ids):
        self.calls.append(list(ad_account_ids))
        if self._results_factory:
            return self._results_factory(ad_account_ids)
        return [
            {"ad_account_id": a, "opened": True, "url": f"url:{a}", "error": ""}
            for a in ad_account_ids
        ]


class _FakeRedis:
    """Fake redis: хранит последний observer:runtime payload."""

    def __init__(self):
        self.store: dict[str, str] = {}

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.store:
            return None  # SET NX: ключ уже есть → не перезаписываем
        self.store[key] = value
        return True

    async def get(self, key):
        return self.store.get(key)


@pytest.fixture(autouse=True)
def _reset_prepared():
    # Module-level флаг подготовки — сбрасываем до и после каждого теста (изоляция).
    observer_main._reset_prepared_accounts()
    yield
    observer_main._reset_prepared_accounts()


# Подготовка открывает все кабинеты и пишет статус preparing в observer:runtime.
async def test_prepare_opens_cabinets_and_sets_status():
    gate = _FakeGate()
    redis = _FakeRedis()
    await observer_main._prepare_workspace(
        None, gate=gate, accounts=["111", "222"], redis_client=redis, tg_client=None
    )
    assert gate.calls == [["111", "222"]]
    payload = json.loads(redis.store["observer:runtime"])
    assert payload["active_phase"] == "preparing"
    assert payload["status"] == "running"  # preparing нормализуется в running
    assert payload["worker_status"] == "preparing"
    assert "Подготавливаю" in payload["status_message"]
    assert payload["accounts_total"] == 2


# Повторная подготовка того же набора (даже в другом порядке) — пропуск, gate не дёргается.
async def test_prepare_idempotent_same_set():
    gate = _FakeGate()
    redis = _FakeRedis()
    await observer_main._prepare_workspace(
        None, gate=gate, accounts=["111", "222"], redis_client=redis, tg_client=None
    )
    await observer_main._prepare_workspace(
        None, gate=gate, accounts=["222", "111"], redis_client=redis, tg_client=None
    )
    assert gate.calls == [["111", "222"]]  # ровно один раз


# Смена набора кабинетов (активирован новый оффер) → переподготовка.
async def test_prepare_reprepares_on_set_change():
    gate = _FakeGate()
    redis = _FakeRedis()
    await observer_main._prepare_workspace(
        None, gate=gate, accounts=["111"], redis_client=redis, tg_client=None
    )
    await observer_main._prepare_workspace(
        None, gate=gate, accounts=["111", "333"], redis_client=redis, tg_client=None
    )
    assert gate.calls == [["111"], ["111", "333"]]


# Если ни один кабинет не открылся — НЕ помечаем подготовленным (повторит на след. цикле).
async def test_prepare_not_marked_when_none_opened():
    def all_failed(ids):
        return [{"ad_account_id": a, "opened": False, "url": "", "error": "boom"} for a in ids]

    gate = _FakeGate(results_factory=all_failed)
    redis = _FakeRedis()
    await observer_main._prepare_workspace(
        None, gate=gate, accounts=["111"], redis_client=redis, tg_client=None
    )
    await observer_main._prepare_workspace(
        None, gate=gate, accounts=["111"], redis_client=redis, tg_client=None
    )
    assert gate.calls == [["111"], ["111"]]  # повторил, т.к. ничего не открылось


# Сбой open_cabinet_tabs не валит подготовку и не помечает prepared (скан откроет вкладки сам).
async def test_prepare_survives_gate_exception():
    class _BoomGate(_FakeGate):
        async def open_cabinet_tabs(self, ad_account_ids):
            self.calls.append(list(ad_account_ids))
            raise RuntimeError("grpc down")

    gate = _BoomGate()
    redis = _FakeRedis()
    await observer_main._prepare_workspace(
        None, gate=gate, accounts=["111"], redis_client=redis, tg_client=None
    )
    await observer_main._prepare_workspace(
        None, gate=gate, accounts=["111"], redis_client=redis, tg_client=None
    )
    assert gate.calls == [["111"], ["111"]]  # не помечен prepared → повтор


# Дедуп TG-уведомлений подготовки по набору: первый раз — да, повтор того же набора — нет.
async def test_prepare_tg_allowed_dedup():
    redis = _FakeRedis()
    accounts = frozenset({"111", "222"})
    assert await observer_main._prepare_tg_allowed(redis, accounts) is True  # первый раз
    assert await observer_main._prepare_tg_allowed(redis, accounts) is False  # дедуп окна
    # Другой набор кабинетов → уведомление разрешено.
    assert await observer_main._prepare_tg_allowed(redis, frozenset({"333"})) is True
    # Redis недоступен → не теряем уведомление (лучше уведомить).
    assert await observer_main._prepare_tg_allowed(None, accounts) is True
