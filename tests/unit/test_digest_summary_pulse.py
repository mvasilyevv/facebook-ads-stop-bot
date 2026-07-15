# -*- coding: utf-8 -*-
"""Unit-тесты AI-резюме дайджеста (2b) и «пульса кабинета» (2c) — без БД и сети."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.digest_scheduler.main import (
    _due_pulse_slot,
    parse_pulse_slots,
    pulse_sent_key,
    run_pulse_tick,
)
from core.ai_assistant.digest_summary import summarize_digest
from core.ai_assistant.pulse import PulseSignals, build_pulse
from core.telegram.digest_builder import DigestPayload

_NOW = datetime(2026, 7, 15, 16, 30, tzinfo=timezone.utc)


def _payload() -> DigestPayload:
    return DigestPayload(
        window_start_utc=datetime(2026, 7, 14, 9, 0, tzinfo=timezone.utc),
        window_end_utc=datetime(2026, 7, 15, 9, 0, tzinfo=timezone.utc),
        alerts_warning_count=2,
        alerts_stop_count=1,
        total_spend_window_usd=Decimal("12.50"),
    )


def _ai(text: str | None = "Вывод: день спокойный.") -> MagicMock:
    client = MagicMock()
    client.is_available = True
    resp = MagicMock()
    resp.text = text
    client.chat = AsyncMock(return_value=resp)
    return client


# Резюме: happy path — AI вернул текст, он и отдаётся
@pytest.mark.asyncio
async def test_summary_happy_path() -> None:
    with patch("core.ai_assistant.digest_summary.get_ai_client", return_value=_ai()):
        assert await summarize_digest(_payload()) == "Вывод: день спокойный."


# Резюме: AI-провайдеры не настроены → None, дайджест уходит как раньше
@pytest.mark.asyncio
async def test_summary_none_when_ai_unavailable() -> None:
    client = MagicMock()
    client.is_available = False
    with patch("core.ai_assistant.digest_summary.get_ai_client", return_value=client):
        assert await summarize_digest(_payload()) is None


# Резюме: кэш в Redis — второй вызов за день не дёргает AI
@pytest.mark.asyncio
async def test_summary_cache_hit_skips_ai() -> None:
    redis = MagicMock()
    redis.get = AsyncMock(return_value="кэшированный вывод")
    ai = _ai()
    with patch("core.ai_assistant.digest_summary.get_ai_client", return_value=ai):
        result = await summarize_digest(_payload(), redis_client=redis)
    assert result == "кэшированный вывод"
    ai.chat.assert_not_awaited()


# Слоты: парсинг строки конфига терпим к мусору и сортирует
def test_parse_pulse_slots() -> None:
    assert parse_pulse_slots("16:00, 12:00,мусор,25:00,20:30") == [(12, 0), (16, 0), (20, 30)]
    assert parse_pulse_slots("") == []


# Слоты: выбирается последний наступивший, окно — от предыдущего слота
def test_due_pulse_slot_window() -> None:
    slots = [(12, 0), (16, 0), (20, 0)]
    due = _due_pulse_slot(_NOW, slots)  # 16:30 UTC
    assert due is not None
    slot, window_start = due
    assert slot == (16, 0)
    assert window_start.hour == 12 and window_start.minute == 0


# Слоты: до первого слота дня пульс не должен ничего делать
def test_due_pulse_slot_none_before_first() -> None:
    early = datetime(2026, 7, 15, 8, 0, tzinfo=timezone.utc)
    assert _due_pulse_slot(early, [(12, 0), (16, 0)]) is None


# Ключ дедупа содержит дату и слот
def test_pulse_sent_key_format() -> None:
    assert pulse_sent_key(_NOW, (16, 0)) == "pulse:sent:2026-07-15:1600"


# Пульс: сигналов нет → None ещё до обращения к AI
@pytest.mark.asyncio
async def test_build_pulse_quiet_without_signals() -> None:
    with (
        patch("core.ai_assistant.pulse.collect_pulse_signals", new=AsyncMock(return_value=None)),
        patch("core.ai_assistant.pulse.get_ai_client") as gac,
    ):
        assert await build_pulse(MagicMock(), since=_NOW, now=_NOW) is None
    gac.assert_not_called()


# Пульс: сигналы есть, AI лёг → детерминированный фолбэк с фактами (не молчание!)
@pytest.mark.asyncio
async def test_build_pulse_fallback_when_ai_down() -> None:
    signals = PulseSignals(
        window_start_utc=_NOW,
        window_end_utc=_NOW,
        stop_count=2,
        warning_count=0,
        failed_tasks_count=1,
        top_stops=[("CR2_CR002", "GH_CR2", ["cpl_stop"])],
    )
    client = MagicMock()
    client.is_available = False
    with (
        patch("core.ai_assistant.pulse.collect_pulse_signals", new=AsyncMock(return_value=signals)),
        patch("core.ai_assistant.pulse.get_ai_client", return_value=client),
    ):
        result = await build_pulse(MagicMock(), since=_NOW, now=_NOW)
    assert result is not None
    assert "Пульс кабинета" in result
    assert "CR2_CR002" in result


# Веб-пульс обязан возвращать plain text, даже если модель проигнорировала prompt
# и прислала Telegram HTML (реальный регресс: в виджете были видны буквальные <b>).
@pytest.mark.asyncio
async def test_build_pulse_web_strips_model_html() -> None:
    signals = PulseSignals(
        window_start_utc=_NOW,
        window_end_utc=_NOW,
        stop_count=1,
        warning_count=0,
        failed_tasks_count=0,
        top_stops=[("CR2_CR002", "CR2", ["cpr_stop"])],
    )
    model_text = (
        "Требуется действие: обнаружен 1 STOP за последний час — <b>CR2_CR002 [CR2]</b> (cpr_stop)."
    )
    with (
        patch("core.ai_assistant.pulse.collect_pulse_signals", new=AsyncMock(return_value=signals)),
        patch("core.ai_assistant.pulse.get_ai_client", return_value=_ai(model_text)) as gac,
    ):
        result = await build_pulse(MagicMock(), since=_NOW, now=_NOW, html=False)

    assert result == (
        "Остановлено 1 объявление за последний час — CR2_CR002 · оффер CR2 — причина: дорогая рега."
    )
    assert "STOP" not in result
    assert "cpr_stop" not in result
    system = gac.return_value.chat.call_args.kwargs["system"]
    assert "plain text" in system


# has_signal: одиночные warnings не будят, стоп/фейл/шквал warnings — будят
def test_pulse_has_signal_thresholds() -> None:
    base = dict(window_start_utc=_NOW, window_end_utc=_NOW)
    assert not PulseSignals(
        stop_count=0, warning_count=2, failed_tasks_count=0, **base
    ).has_signal()
    assert PulseSignals(stop_count=1, warning_count=0, failed_tasks_count=0, **base).has_signal()
    assert PulseSignals(stop_count=0, warning_count=0, failed_tasks_count=1, **base).has_signal()
    assert PulseSignals(stop_count=0, warning_count=3, failed_tasks_count=0, **base).has_signal()


def _pulse_env(*, enabled: bool = True) -> MagicMock:
    settings = MagicMock()
    settings.ai_pulse_enabled = enabled
    settings.ai_pulse_slots_utc = "12:00,16:00,20:00"
    return settings


# run_pulse_tick: фича выключена (дефолт) — ничего не происходит
@pytest.mark.asyncio
async def test_pulse_tick_disabled_by_default() -> None:
    with patch("apps.digest_scheduler.main.get_settings", return_value=_pulse_env(enabled=False)):
        status = await run_pulse_tick(
            engine=MagicMock(),
            redis_client=MagicMock(),
            tg_client_factory=MagicMock(),
            now=_NOW,
        )
    assert status == "disabled"


# run_pulse_tick: сигналов нет → слот закрывается молча, сообщение не шлётся
@pytest.mark.asyncio
async def test_pulse_tick_quiet_marks_slot() -> None:
    redis = MagicMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock()
    cfg = MagicMock()
    cfg.bot_token = "token"
    factory = MagicMock()
    with (
        patch("apps.digest_scheduler.main.get_settings", return_value=_pulse_env()),
        patch("apps.digest_scheduler.main.load_telegram_config", new=AsyncMock(return_value=cfg)),
        patch(
            "apps.digest_scheduler.main.load_active_recipients",
            new=AsyncMock(return_value=[MagicMock(chat_id=1)]),
        ),
        patch("apps.digest_scheduler.main.build_pulse", new=AsyncMock(return_value=None)),
    ):
        status = await run_pulse_tick(
            engine=MagicMock(), redis_client=redis, tg_client_factory=factory, now=_NOW
        )
    assert status == "quiet"
    redis.set.assert_awaited()  # слот закрыт
    factory.assert_not_called()  # TG-клиент даже не создавался


# run_pulse_tick: сигналы есть → отчёт уходит получателям, слот закрывается
@pytest.mark.asyncio
async def test_pulse_tick_sends_report() -> None:
    redis = MagicMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock()
    cfg = MagicMock()
    cfg.bot_token = "token"
    tg = MagicMock()
    tg.send_message = AsyncMock()
    tg.close = AsyncMock()
    with (
        patch("apps.digest_scheduler.main.get_settings", return_value=_pulse_env()),
        patch("apps.digest_scheduler.main.load_telegram_config", new=AsyncMock(return_value=cfg)),
        patch(
            "apps.digest_scheduler.main.load_active_recipients",
            new=AsyncMock(return_value=[MagicMock(chat_id=1)]),
        ),
        patch(
            "apps.digest_scheduler.main.build_pulse",
            new=AsyncMock(return_value="📟 <b>Пульс кабинета</b>\n2 стопа"),
        ),
    ):
        status = await run_pulse_tick(
            engine=MagicMock(),
            redis_client=redis,
            tg_client_factory=MagicMock(return_value=tg),
            now=_NOW,
        )
    assert status == "sent"
    assert "Пульс" in tg.send_message.call_args.kwargs["text"]
    redis.set.assert_awaited()


# run_pulse_tick: повторный тик того же слота — дедуп по Redis-ключу
@pytest.mark.asyncio
async def test_pulse_tick_already_sent() -> None:
    redis = MagicMock()
    redis.get = AsyncMock(return_value="1")
    with patch("apps.digest_scheduler.main.get_settings", return_value=_pulse_env()):
        status = await run_pulse_tick(
            engine=MagicMock(), redis_client=redis, tg_client_factory=MagicMock(), now=_NOW
        )
    assert status == "already_sent"
