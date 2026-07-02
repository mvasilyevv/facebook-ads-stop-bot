# -*- coding: utf-8 -*-
"""MID X-16: разлогин/чекпоинт Vision-профиля в observer.

browser-agent детектит redirect на login.php/checkpoint, HTML вместо JSON или Graph 190
с login-subcode и отдаёт empty_reason='login_required'. Money-критично (инцидент 01.07 —
канал умер молча): такой цикл — НЕ «пустой кабинет», а слепота канала. Проверяем:
- login_required-скан → outcome='error' (не 'empty'): resolve_scan_mode даёт CALM, не IDLE,
  и degraded-счётчик растёт (авто-стоп не «спит» в IDLE при живом инциденте);
- поднимается deduped алерт «Vision-профиль разлогинен» с re-arm при недоставке;
- обычный пустой скан (no_active_ads) НЕ триггерит ни error, ни алерт.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

import apps.observer_worker.main as ow
from apps.observer_worker.main import ScanCycleOutput
from core.observer.adaptive_interval import resolve_scan_mode

# ====================== _maybe_alert_login_required ======================


# Дедуп свободен + notify_recipients доставил → True, текст про разлогин/ре-логин
@pytest.mark.asyncio
async def test_login_required_alert_delivers_via_recipients(monkeypatch):
    spy = AsyncMock(return_value=True)
    monkeypatch.setattr(ow, "notify_recipients", spy)
    redis = AsyncMock()
    redis.set = AsyncMock(return_value=True)  # SET NX прошёл — окно свободно

    ok = await ow._maybe_alert_login_required(object(), redis, ad_account_id="act_777")

    assert ok is True
    spy.assert_awaited_once()
    text = spy.await_args.kwargs["text"]
    low = text.lower()
    assert "разлогин" in low
    assert "ре-логин" in low or "залогин" in low
    assert "act_777" in text  # кабинет в тексте
    redis.delete.assert_not_awaited()  # при успехе дедуп НЕ снимается


# Дедуп уже стоит (SET NX вернул falsy) → notify_recipients не зовётся, False
@pytest.mark.asyncio
async def test_login_required_alert_dedup_skips_send(monkeypatch):
    spy = AsyncMock(return_value=True)
    monkeypatch.setattr(ow, "notify_recipients", spy)
    redis = AsyncMock()
    redis.set = AsyncMock(return_value=None)  # NX: ключ уже существует

    ok = await ow._maybe_alert_login_required(object(), redis, ad_account_id=None)

    assert ok is False
    spy.assert_not_awaited()


# notify_recipients вернул False → warning + сброс дедупа (ретрай на след. цикле)
@pytest.mark.asyncio
async def test_login_required_alert_undelivered_rearms(monkeypatch, caplog):
    spy = AsyncMock(return_value=False)
    monkeypatch.setattr(ow, "notify_recipients", spy)
    redis = AsyncMock()
    redis.set = AsyncMock(return_value=True)

    with caplog.at_level("WARNING"):
        ok = await ow._maybe_alert_login_required(object(), redis, ad_account_id="act_1")

    assert ok is False
    spy.assert_awaited_once()
    assert any("не доставлен" in r.getMessage().lower() for r in caplog.records)
    redis.delete.assert_awaited_once_with(ow.LOGIN_REQUIRED_ALERT_DEDUP_KEY)


# redis None → money-критичный алерт всё равно шлём (в отличие от degraded)
@pytest.mark.asyncio
async def test_login_required_alert_without_redis_still_sends(monkeypatch):
    spy = AsyncMock(return_value=True)
    monkeypatch.setattr(ow, "notify_recipients", spy)

    ok = await ow._maybe_alert_login_required(object(), None, ad_account_id="act_9")

    assert ok is True
    spy.assert_awaited_once()


# ====================== _run_account_scan: login_required → error + alert ======================


@pytest.fixture
def _stub_scan_db(monkeypatch):
    """Заглушки DB/Redis-хелперов _run_account_scan, чтобы тестировать ветку без БД."""
    monkeypatch.setattr(ow, "_begin_scan_run", AsyncMock(return_value=101))
    monkeypatch.setattr(ow, "_finish_scan_run", AsyncMock())
    monkeypatch.setattr(ow, "_publish_scan_finished", AsyncMock())
    monkeypatch.setattr(ow, "_publish_runtime_status", AsyncMock())
    # process_scan_rows не должен вызываться при пустом скане — но подстрахуемся.
    monkeypatch.setattr(ow, "process_scan_rows", AsyncMock())


class _FakeGate:
    """Fake ScannerGate с заранее заданным ScanCycleOutput."""

    def __init__(self, output: ScanCycleOutput):
        self._output = output

    async def run_one_scan(self, **kwargs) -> ScanCycleOutput:
        return self._output

    async def open_cabinet_tabs(self, ad_account_ids):
        return []


# login_required-скан → outcome='error' (НЕ 'empty') + deduped алерт вызван
@pytest.mark.asyncio
async def test_run_account_scan_login_required_marks_error_and_alerts(_stub_scan_db, monkeypatch):
    alert_spy = AsyncMock(return_value=True)
    monkeypatch.setattr(ow, "_maybe_alert_login_required", alert_spy)
    gate = _FakeGate(ScanCycleOutput(rows=[], empty_reason="login_required"))

    summary = await ow._run_account_scan(
        object(),
        gate=gate,
        config={"campaign_ids": ["c1"], "owner_campaign_tag": "MV"},
        auto_recover_page=True,
        redis_client=AsyncMock(),
        tg_client=None,
        ad_account_id="act_5",
    )

    assert summary["outcome"] == "error"  # не 'empty' → degraded-детектор его увидит
    assert summary["error"] == "login_required"
    alert_spy.assert_awaited_once()
    assert alert_spy.await_args.kwargs["ad_account_id"] == "act_5"


# Обычный пустой скан (no_active_ads) → outcome='empty', алерт НЕ вызван (регресс-защита)
@pytest.mark.asyncio
async def test_run_account_scan_normal_empty_no_alert(_stub_scan_db, monkeypatch):
    alert_spy = AsyncMock(return_value=True)
    monkeypatch.setattr(ow, "_maybe_alert_login_required", alert_spy)
    gate = _FakeGate(ScanCycleOutput(rows=[], empty_reason="no_active_ads"))

    summary = await ow._run_account_scan(
        object(),
        gate=gate,
        config={"campaign_ids": ["c1"], "owner_campaign_tag": "MV"},
        auto_recover_page=True,
        redis_client=AsyncMock(),
        tg_client=None,
        ad_account_id="act_5",
    )

    assert summary["outcome"] == "empty"
    alert_spy.assert_not_awaited()


# ====================== инвариант адаптива: login_required = error → CALM, не IDLE ======================


# Money-инвариант: login_required-цикл (outcome='error') держит CALM-темп, НЕ уходит в IDLE
def test_login_required_summary_resolves_to_calm_not_idle() -> None:
    # Ключевое поле — outcome='error' (его выставляет _run_account_scan при login_required).
    summary = {"outcome": "error", "rows_with_offer": 0, "alerts_stop": 0, "alerts_warning": 0}
    mode = resolve_scan_mode(summary)
    assert mode == "CALM"
    assert mode != "IDLE"  # иначе горящее объявление ждёт дольше при слепом канале
