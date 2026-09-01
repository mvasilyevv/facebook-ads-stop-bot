# -*- coding: utf-8 -*-
"""Money-инвариант: пиксель залива сверяется с пикселем оффера (issue #359).

До этой правки ``_require_offer_scope`` проверял только привязку кабинета и
валюту CPA — пиксель не сверялся нигде. Тихий случай: пиксель синтаксически
валиден и принадлежит кабинету, но чужой — кампания создаётся с неверным
событием оптимизации, и залив уходит в открутку незамеченным.

``_evaluate_offer_scope`` — чистое ядро решения без БД (аналог
``_account_context_rejection`` для контекста кабинета), поэтому тесты не
трогают Postgres.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from apps.api.routers.v1.campaigns_create import _evaluate_offer_scope


def _call(
    *,
    account_is_configured: bool = True,
    rule_currency: str = "",
    cpa_threshold_is_set: bool = False,
    account_currency: str = "USD",
    offer_pixel_id: str = "",
    submitted_pixel_id: str = "",
    pixel_confirmed: bool = False,
) -> None:
    _evaluate_offer_scope(
        account_is_configured=account_is_configured,
        rule_currency=rule_currency,
        cpa_threshold_is_set=cpa_threshold_is_set,
        account_currency=account_currency,
        offer_pixel_id=offer_pixel_id,
        submitted_pixel_id=submitted_pixel_id,
        pixel_confirmed=pixel_confirmed,
    )


def test_pixel_mismatch_is_rejected_before_dispatch() -> None:
    """Расхождение без подтверждения — 409 ДО постановки в очередь."""

    with pytest.raises(HTTPException) as excinfo:
        _call(offer_pixel_id="111", submitted_pixel_id="222")

    assert excinfo.value.status_code == 409
    detail = str(excinfo.value.detail)
    # Причина называет оператору оба id — что ожидалось и что пришло.
    assert "111" in detail
    assert "222" in detail


def test_matching_pixel_passes() -> None:
    """Пиксель совпадает с офферным — сверять нечего, залив идёт дальше."""

    _call(offer_pixel_id="111", submitted_pixel_id="111")


def test_offer_without_pixel_does_not_block_launch() -> None:
    """Оффер без записанного пикселя (nullable) — сверить не с чем, не блокируем."""

    _call(offer_pixel_id="", submitted_pixel_id="999")


def test_operator_confirmation_lifts_the_pixel_rejection() -> None:
    """Мультипиксельный кабинет: осознанный выбор другого пикселя не блокируется.

    ``pixel_confirmed=True`` — явное согласие оператора с шага «Идентичность»,
    а не выключатель сверки: расхождение без него по-прежнему падает (тест выше).
    """

    _call(offer_pixel_id="111", submitted_pixel_id="222", pixel_confirmed=True)


def test_cabinet_scope_rejection_wins_over_pixel_confirmation() -> None:
    """Подтверждение пикселя не открывает путь в незапривязанный кабинет."""

    with pytest.raises(HTTPException) as excinfo:
        _call(
            account_is_configured=False,
            offer_pixel_id="111",
            submitted_pixel_id="222",
            pixel_confirmed=True,
        )

    assert excinfo.value.status_code == 409
    assert excinfo.value.detail == "Выбранный кабинет не привязан к офферу"


def test_currency_rejection_wins_over_pixel_confirmation() -> None:
    """Подтверждение пикселя не открывает путь при несовпадении валюты CPA."""

    with pytest.raises(HTTPException) as excinfo:
        _call(
            rule_currency="EUR",
            account_currency="USD",
            offer_pixel_id="111",
            submitted_pixel_id="222",
            pixel_confirmed=True,
        )

    assert excinfo.value.status_code == 409
    assert "Валюта CPA" in str(excinfo.value.detail)
