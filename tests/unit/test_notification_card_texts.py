# -*- coding: utf-8 -*-
"""Смысл операторских карточек: что случилось, сколько денег, что делать.

Тесты намеренно проверяют смысл, а не дословную строку: имя объявления,
сумму с валютой, наличие действия и отсутствие внутренних кодов.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from core.meta_api.duplicate_incidents import project_duplicate_incident_in_transaction
from core.observer.writers import (
    _CURRENCY_UNCONFIRMED,
    _incident_action_line,
    _incident_lines,
    _incident_risk,
    _incident_summary,
    _incident_title,
)
from core.telegram.notification_renderer import render_notification
from core.telegram.schemas import NotificationCardFacts, NotificationEventSpec

_STOP_METRICS = {
    "spend": "18.40",
    "clicks": 43,
    "registrations": 3,
    "deposits": 0,
    "_hits": [
        {
            "code": "cpr_stop",
            "stage": "stop",
            "value": "9.56",
            "threshold": "3.00",
        }
    ],
}


def _render(
    *,
    metrics: dict,
    codes: tuple[str, ...],
    currency: str | None,
    auto_stop: bool,
    severity: str = "critical",
) -> str:
    summary = _incident_summary(metrics, codes, currency=currency)
    facts = NotificationCardFacts(
        title=_incident_title("CR2_CR005", codes),
        summary=summary,
        lines=[
            *_incident_lines(
                metrics,
                currency=currency,
                currency_reason_stated=_CURRENCY_UNCONFIRMED in summary,
            ),
            _incident_action_line(auto_stop=auto_stop),
        ],
        risk=_incident_risk(codes),
    )
    event = NotificationEventSpec(
        event_type="incident_stop",
        severity=severity,
        facts=facts,
        dedupe_key="card-text-check",
    )
    return render_notification(event).text


def test_stop_card_names_ad_reason_money_and_action() -> None:
    text = _render(
        metrics=_STOP_METRICS,
        codes=("cpr_stop",),
        currency="KES",
        auto_stop=True,
    )

    assert "CR2_CR005" in text  # какое объявление
    assert "Дорогая рега" in text  # что случилось
    assert "9.56 KES" in text and "3.00 KES" in text  # число и порог с валютой
    assert "18.40 KES" in text  # сколько потрачено
    assert "43 клика" in text and "3 регистрации" in text and "депозитов нет" in text
    assert "Отключаю объявление" in text  # что делает система
    assert "Риск:" in text  # чем это грозит
    # Внутренние коды и англицизмы в карточку не попадают.
    for forbidden in ("CPR_STOP", "cpr_stop", "Spend", "STOP", "WARNING"):
        assert forbidden not in text


def test_stop_card_fits_renderer_limits() -> None:
    text = _render(
        metrics=_STOP_METRICS,
        codes=("cpr_stop",),
        currency="KES",
        auto_stop=True,
    )

    assert len(text) <= 700
    assert len(text.splitlines()) <= 6  # заголовок + максимум пять строк фактов


def test_warning_card_says_no_action_required() -> None:
    metrics = {
        **_STOP_METRICS,
        "_hits": [
            {
                "code": "spend_no_dep_range",
                "stage": "warning",
                "value": "42.50",
                "threshold": "40.00",
            }
        ],
    }

    text = _render(
        metrics=metrics,
        codes=("spend_no_dep_range",),
        currency="USD",
        auto_stop=False,
        severity="warning",
    )

    assert "42.50% от CPA" in text
    assert "при пороге 40.00%" in text
    # Процент правила spend-range не должен получать валюту рядом с числом.
    assert "42.50% от CPA USD" not in text
    assert "действий от тебя не требуется" in text


def test_card_hides_money_without_confirmed_currency() -> None:
    text = _render(
        metrics=_STOP_METRICS,
        codes=("cpr_stop",),
        currency=None,
        auto_stop=True,
    )

    assert "18.40" not in text
    assert "9.56" not in text
    # Причина названа ровно один раз: два одинаковых объяснения подряд в
    # короткой карточке читаются как сбой рендера, а не как забота.
    assert text.count(_CURRENCY_UNCONFIRMED) == 1
    assert "Расход не показан" in text
    # Метрики без денег остаются: карточка не превращается в пустую.
    assert "43 клика" in text


@pytest.mark.asyncio
async def test_duplicate_incident_card_counts_objects_and_gives_action(monkeypatch) -> None:
    import core.meta_api.duplicate_incidents as module

    notify = AsyncMock(return_value=True)
    monkeypatch.setattr(module, "notify_recurring_incident_in_transaction", notify)

    await project_duplicate_incident_in_transaction(
        object(),  # type: ignore[arg-type]
        task_id=42,
        checkpoint={"created_ids": {"adsets": ["a", "b"], "ads": ["c"]}},
        stage="partial",
    )

    kwargs = notify.await_args.kwargs
    assert kwargs["incident_key"] == "task:duplicate-adset:42"
    assert kwargs["event_type"] == "duplicate_adset_partial"
    assert kwargs["severity"] == "critical"
    assert "#42" in kwargs["summary"]
    assert "3 объекта" in kwargs["summary"]
    assert any("Ads Manager" in line for line in kwargs["lines"])
    assert kwargs["risk"]
    for forbidden in ("PAUSE", "recovery", "checkpoint"):
        assert forbidden not in " ".join([kwargs["title"], kwargs["summary"], *kwargs["lines"]])
