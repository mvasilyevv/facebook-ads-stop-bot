"""Тесты фазы «подготовка рабочего места» observer.

Перед сканом observer открывает вкладки Ads Manager кабинетов активных офферов
(manage/campaigns + колонки). Идемпотентно по
набору кабинетов: повтор того же набора — пропуск, смена набора — переподготовка.
"""

import pytest

import core.observer.cabinet_tab_incident as _cabinet_tab_module
from apps.observer_worker import main as observer_main


class _FakeGate:
    """Fake ScannerGate: фиксирует вызовы open_cabinet_tabs, возвращает заданное."""

    def __init__(self, results_factory=None):
        self.calls: list[list[str]] = []
        self._results_factory = results_factory

    async def run_one_scan(self, **kwargs):  # в этих тестах не используется
        from apps.observer_worker.main import ScanCycleOutput

        return ScanCycleOutput(rows=[], metrics_contract_revision=0)

    async def open_cabinet_tabs(self, ad_account_ids):
        self.calls.append(list(ad_account_ids))
        if self._results_factory:
            return self._results_factory(ad_account_ids)
        return [
            {
                "ad_account_id": a,
                "opened": True,
                "url": (f"https://adsmanager.facebook.com/adsmanager/manage/campaigns?act={a}"),
                "error": "",
            }
            for a in ad_account_ids
        ]


@pytest.fixture(autouse=True)
def _reset_prepared(monkeypatch):
    # Module-level флаг подготовки — сбрасываем до и после каждого теста (изоляция).
    calls = {"opened": [], "resolved": []}

    async def _opened(_engine, **kwargs):
        calls["opened"].append(kwargs)
        return True

    async def _resolved(_engine, **kwargs):
        calls["resolved"].append(kwargs)
        return True

    monkeypatch.setattr(_cabinet_tab_module, "notify_recurring_incident", _opened)
    monkeypatch.setattr(_cabinet_tab_module, "resolve_recurring_incident", _resolved)
    observer_main._reset_prepared_accounts()
    yield calls
    observer_main._reset_prepared_accounts()


# Подготовка открывает все кабинеты.
async def test_prepare_opens_cabinets():
    gate = _FakeGate()
    await observer_main._prepare_workspace(None, gate=gate, accounts=["111", "222"])
    assert gate.calls == [["111", "222"]]


# Повторная подготовка того же набора (даже в другом порядке) — пропуск, gate не дёргается.
async def test_prepare_idempotent_same_set():
    gate = _FakeGate()
    await observer_main._prepare_workspace(None, gate=gate, accounts=["111", "222"])
    await observer_main._prepare_workspace(None, gate=gate, accounts=["222", "111"])
    assert gate.calls == [["111", "222"]]  # ровно один раз


# Смена набора кабинетов (активирован новый оффер) → переподготовка.
async def test_prepare_reprepares_on_set_change():
    gate = _FakeGate()
    await observer_main._prepare_workspace(None, gate=gate, accounts=["111"])
    await observer_main._prepare_workspace(None, gate=gate, accounts=["111", "333"])
    assert gate.calls == [["111"], ["111", "333"]]


# Если ни один кабинет не открылся — НЕ помечаем подготовленным (повторит на след. цикле).
async def test_prepare_not_marked_when_none_opened():
    def all_failed(ids):
        return [{"ad_account_id": a, "opened": False, "url": "", "error": "boom"} for a in ids]

    gate = _FakeGate(results_factory=all_failed)
    await observer_main._prepare_workspace(None, gate=gate, accounts=["111"])
    await observer_main._prepare_workspace(None, gate=gate, accounts=["111"])
    assert gate.calls == [["111"], ["111"]]  # повторил, т.к. ничего не открылось


async def test_prepare_retries_partial_set_and_opens_incident(_reset_prepared):
    def one_failed(ids):
        return [
            {
                "ad_account_id": account_id,
                "opened": account_id == "111",
                "url": (
                    f"https://adsmanager.facebook.com/adsmanager/manage/campaigns?act={account_id}"
                    if account_id == "111"
                    else ""
                ),
                "error": "cabinet_tab_not_confirmed" if account_id != "111" else "",
            }
            for account_id in ids
        ]

    gate = _FakeGate(results_factory=one_failed)
    await observer_main._prepare_workspace(None, gate=gate, accounts=["111", "222"])
    await observer_main._prepare_workspace(None, gate=gate, accounts=["111", "222"])

    assert gate.calls == [["111", "222"], ["111", "222"]]
    assert {call["resource_id"] for call in _reset_prepared["opened"]} == {"222"}
    assert any("111" in call["incident_key"] for call in _reset_prepared["resolved"])


async def test_prepare_rejects_opened_result_with_wrong_final_cabinet(_reset_prepared):
    gate = _FakeGate(
        results_factory=lambda _ids: [
            {
                "ad_account_id": "111",
                "opened": True,
                "url": ("https://adsmanager.facebook.com/adsmanager/manage/campaigns?act=999"),
                "error": "",
            }
        ]
    )

    await observer_main._prepare_workspace(None, gate=gate, accounts=["111"])
    await observer_main._prepare_workspace(None, gate=gate, accounts=["111"])

    assert gate.calls == [["111"], ["111"]]
    assert all(call["resource_id"] == "111" for call in _reset_prepared["opened"])


@pytest.mark.parametrize(
    "url",
    [
        "https://www.facebook.com/login.php?next=adsmanager&act=111",
        "https://adsmanager.facebook.com/adsmanager/manage/campaigns?act=999",
        "http://adsmanager.facebook.com/adsmanager/manage/campaigns?act=111",
        "https://user@adsmanager.facebook.com/adsmanager/manage/campaigns?act=111",
        "https://www.facebook.com/business/adsmanager/manage?act=111",
        "https://adsmanager.facebook.com/adsmanager/manage/campaigns?act=111&act=999",
    ],
)
def test_cabinet_tab_confirmation_rejects_untrusted_final_url(url):
    assert not observer_main._cabinet_tab_is_confirmed(
        {"ad_account_id": "111", "opened": True, "url": url},
        account_id="111",
    )


# Сбой open_cabinet_tabs не валит подготовку и не помечает prepared (скан откроет вкладки сам).
async def test_prepare_survives_gate_exception():
    class _BoomGate(_FakeGate):
        async def open_cabinet_tabs(self, ad_account_ids):
            self.calls.append(list(ad_account_ids))
            raise RuntimeError("grpc down")

    gate = _BoomGate()
    await observer_main._prepare_workspace(None, gate=gate, accounts=["111"])
    await observer_main._prepare_workspace(None, gate=gate, accounts=["111"])
    assert gate.calls == [["111"], ["111"]]  # не помечен prepared → повтор
