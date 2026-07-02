# -*- coding: utf-8 -*-
"""Unit-тесты H-4: replay-защита кнопки dis:<fb>:<token> в handle_dis_callback.

Кнопка отключения несёт token = open_state_token инцидента, в рамках которого был
отправлен алерт. Перед созданием pause-задачи handler сверяет token с текущим
open_state_token в ad_alert_state:
- совпадает → задача создаётся (штатный путь);
- не совпадает / NULL / нет строки state → отказ + answerCallbackQuery, задачи нет.

Эскалация warning_sent→stop_sent сохраняет open_state_token (state_machine.decide),
поэтому старая WARNING-кнопка того же инцидента остаётся валидной (инвариант HIGH #10).

Мокаем: load_alert_state_by_fb_ad_id (импорт внутри функции из core.observer.queries),
_create_toggle_mutation и mark_alert_state_claimed (side-эффекты) — БД не трогаем.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

import core.telegram.handlers.alerts as alerts
from core.observer.queries import AdAlertSnapshot


def _snapshot(
    *, open_state_token: uuid.UUID | None, alert_state: str = "stop_sent"
) -> AdAlertSnapshot:
    """Собрать FSM-снимок для fb_ad_id='AD1' с заданным open_state_token."""
    return AdAlertSnapshot(
        ad_id=uuid.uuid4(),
        fb_ad_id="AD1",
        alert_state=alert_state,
        current_stage="stop" if alert_state == "stop_sent" else "warning",
        open_state_token=open_state_token,
        snoozed_until=None,
    )


async def _call_dis(*, token: str, client: AsyncMock) -> AsyncMock:
    """Вызвать handle_dis_callback с замоканной _create_toggle_mutation, вернуть её spy."""
    create_spy = AsyncMock(return_value=42)
    with (
        patch.object(alerts, "_create_toggle_mutation", create_spy),
        patch(
            "core.observer.writers.mark_alert_state_claimed",
            new=AsyncMock(),
        ),
    ):
        await alerts.handle_dis_callback(
            engine=object(),
            client=client,
            cq_id="cq1",
            fb_ad_id="AD1",
            token=token,
            username="owner",
        )
    return create_spy


# Совпадающий token → задача создаётся (штатный регресс-путь)
@pytest.mark.asyncio
async def test_matching_token_creates_task() -> None:
    tok = uuid.uuid4()
    client = AsyncMock()
    with patch(
        "core.observer.queries.load_alert_state_by_fb_ad_id",
        new=AsyncMock(return_value={"AD1": _snapshot(open_state_token=tok)}),
    ):
        create_spy = await _call_dis(token=str(tok), client=client)

    create_spy.assert_awaited_once()
    # ack — про принятую задачу, НЕ про устаревший алерт
    ack_text = client.answer_callback_query.call_args.kwargs.get("text", "") or (
        client.answer_callback_query.call_args.args[1]
        if len(client.answer_callback_query.call_args.args) > 1
        else ""
    )
    assert "устарел" not in ack_text.lower()


# Несовпадающий token (новый инцидент) → отказ, задачи нет
@pytest.mark.asyncio
async def test_mismatched_token_rejected() -> None:
    old_tok = uuid.uuid4()  # token из СТАРОЙ кнопки в истории чата
    new_tok = uuid.uuid4()  # текущий инцидент — уже другой
    client = AsyncMock()
    with patch(
        "core.observer.queries.load_alert_state_by_fb_ad_id",
        new=AsyncMock(return_value={"AD1": _snapshot(open_state_token=new_tok)}),
    ):
        create_spy = await _call_dis(token=str(old_tok), client=client)

    create_spy.assert_not_awaited()
    client.answer_callback_query.assert_awaited_once()
    ack_text = client.answer_callback_query.call_args.kwargs["text"]
    assert "устарел" in ack_text.lower()


# Инцидент закрыт: open_state_token=NULL (объявление восстановилось) → отказ
@pytest.mark.asyncio
async def test_null_token_rejected() -> None:
    old_tok = uuid.uuid4()
    client = AsyncMock()
    with patch(
        "core.observer.queries.load_alert_state_by_fb_ad_id",
        new=AsyncMock(return_value={"AD1": _snapshot(open_state_token=None, alert_state="normal")}),
    ):
        create_spy = await _call_dis(token=str(old_tok), client=client)

    create_spy.assert_not_awaited()
    ack_text = client.answer_callback_query.call_args.kwargs["text"]
    assert "устарел" in ack_text.lower()


# Строки ad_alert_state нет вовсе (объявление больше не в инциденте) → отказ
@pytest.mark.asyncio
async def test_missing_state_rejected() -> None:
    old_tok = uuid.uuid4()
    client = AsyncMock()
    with patch(
        "core.observer.queries.load_alert_state_by_fb_ad_id",
        new=AsyncMock(return_value={}),  # fb_ad_id отсутствует в map
    ):
        create_spy = await _call_dis(token=str(old_tok), client=client)

    create_spy.assert_not_awaited()
    ack_text = client.answer_callback_query.call_args.kwargs["text"]
    assert "устарел" in ack_text.lower()


# Пустой token в кнопке (legacy / битая callback-data) → отказ
@pytest.mark.asyncio
async def test_empty_token_rejected() -> None:
    tok = uuid.uuid4()
    client = AsyncMock()
    with patch(
        "core.observer.queries.load_alert_state_by_fb_ad_id",
        new=AsyncMock(return_value={"AD1": _snapshot(open_state_token=tok)}),
    ):
        create_spy = await _call_dis(token="", client=client)

    create_spy.assert_not_awaited()
    ack_text = client.answer_callback_query.call_args.kwargs["text"]
    assert "устарел" in ack_text.lower()


# Инвариант HIGH #10: warning→stop эскалация сохраняет token → старая WARNING-кнопка РАБОТАЕТ.
# Инцидент стартовал в warning_sent (сгенерирован token T), эскалировал в stop_sent с ТЕМ ЖЕ T.
# Пользователь жмёт старую WARNING-кнопку (dis:AD1:T) — token совпадает с текущим stop_sent → OK.
@pytest.mark.asyncio
async def test_escalation_preserves_token_old_warning_button_works() -> None:
    from core.observer.state_machine import FsmInput, decide

    # Старт инцидента: normal → warning_sent, генерируется token
    t1 = decide(
        FsmInput(
            current_state="normal",
            current_stage=None,
            current_open_token=None,
            warning_rule_codes=("cpa_warn",),
            stop_rule_codes=(),
        )
    )
    warning_token = t1.new_open_token
    assert warning_token is not None

    # Эскалация: warning_sent → stop_sent, token ДОЛЖЕН сохраниться
    t2 = decide(
        FsmInput(
            current_state="warning_sent",
            current_stage="warning",
            current_open_token=warning_token,
            warning_rule_codes=(),
            stop_rule_codes=("cpa_stop",),
        )
    )
    assert t2.new_open_token == warning_token, "эскалация обязана сохранять open_state_token"

    # Текущее состояние — stop_sent с тем же token; пользователь жмёт СТАРУЮ warning-кнопку
    client = AsyncMock()
    with patch(
        "core.observer.queries.load_alert_state_by_fb_ad_id",
        new=AsyncMock(return_value={"AD1": _snapshot(open_state_token=warning_token)}),
    ):
        create_spy = await _call_dis(token=str(warning_token), client=client)

    create_spy.assert_awaited_once()
    ack_text = client.answer_callback_query.call_args.kwargs.get("text", "")
    assert "устарел" not in ack_text.lower()
