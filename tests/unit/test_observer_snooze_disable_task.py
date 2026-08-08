# -*- coding: utf-8 -*-
"""Unit: снуз глушит TG-алерт, но НЕ авто-стоп (MID-2, money).

Проверяем _process_one_row (pipeline) с замоканным I/O:
  - заснуженный ад при STOP всё равно создаёт disable-задачу в той же транзакции,
    но alert не эмитится (apply_fsm_transition получил emit_alert=False);
  - для контраста: НЕ заснуженный ад при STOP и создаёт задачу, и эмитит алерт.

Снуз задуман «не спамить алертами по активному инциденту», а не «выключить авто-стоп».
Ранее _suppress_emit обнулял create_disable_task → заснуженный убыточный ад крутился
без стопа до истечения окна снуза (money-дыра).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from core.domain import AlertStage
from core.observer import pipeline as pl
from core.observer.pipeline import CycleResult, _process_one_row
from core.observer.queries import AdAlertSnapshot, OfferRules
from core.rules.types import RuleEvaluation, RuleHit
from core.scanner.models import ScannedAdRow


def _stop_eval() -> RuleEvaluation:
    """RuleEvaluation с одним STOP-хитом — фиксированный STOP-сценарий."""
    hit = RuleHit(
        code="cpc_stop",
        title="CPC стоп",
        stage=AlertStage.STOP,
        value=Decimal("5"),
        threshold=Decimal("2"),
        summary="CPC превысил порог",
        reason_text="CPC $5 > стоп $2",
    )
    return RuleEvaluation(stage=AlertStage.STOP, warning_hits=(), stop_hits=(hit,))


def _offer() -> OfferRules:
    return OfferRules(
        offer_id=uuid.uuid4(),
        code="CR2",
        name="Test Offer",
        cpa_threshold=Decimal("10"),
        currency="USD",
        frequency_threshold=None,
        stop_percent_of_rule=Decimal("80"),
        warning_percent_of_stop=Decimal("80"),
    )


def _row() -> ScannedAdRow:
    return ScannedAdRow(
        fb_ad_id="9001",
        campaign_name="CR2 | DRC | MV",
        adset_name="adset",
        ad_name="ad",
        delivery_status="ACTIVE",
        spend=Decimal("50"),
    )


def _patch_io(monkeypatch, *, captured: dict) -> None:
    """Мокает всё I/O _process_one_row, собирая вызовы apply_fsm/create_task."""

    def _match_owner(**kwargs):
        return True

    def _match_offer(**kwargs):
        return _offer()

    async def _upsert(*args, **kwargs):
        return uuid.uuid4()

    async def _insert_metrics(*args, **kwargs):
        captured["metrics_currency"] = kwargs["currency"]
        return True

    # evaluator даёт один STOP-хит независимо от метрик — сценарий фиксирован.
    def _evaluate(row, ctx):
        return _stop_eval()

    async def _apply_fsm(
        engine,
        *,
        ad_id,
        transition,
        metrics_snapshot,
        scan_id,
        fb_ad_id,
        ad_account_id=None,
        currency,
    ):
        captured["apply_transition"] = transition
        captured["create_called"] = transition.create_disable_task
        captured["currency"] = currency
        return 777 if transition.create_disable_task else None

    monkeypatch.setattr(pl, "campaign_matches_owner", _match_owner)
    monkeypatch.setattr(pl, "match_offer_for_ad", _match_offer)
    monkeypatch.setattr(pl, "upsert_catalog_hierarchy", _upsert)
    monkeypatch.setattr(pl, "insert_metrics", _insert_metrics)
    monkeypatch.setattr(pl, "evaluate_stop_rules", _evaluate)
    monkeypatch.setattr(pl, "apply_fsm_transition", _apply_fsm)


# Заснуженный ад в stop_sent при STOP: disable-задача СОЗДАЁТСЯ, но алерт НЕ шлётся.
async def test_snoozed_ad_stop_still_creates_disable_task(monkeypatch) -> None:
    cycle_ts = datetime(2026, 7, 2, 12, 0, 0, tzinfo=timezone.utc)
    captured: dict = {}
    _patch_io(monkeypatch, captured=captured)

    ad_id = uuid.uuid4()
    token = uuid.uuid4()
    snapshot = AdAlertSnapshot(
        ad_id=ad_id,
        fb_ad_id="9001",
        alert_state="stop_sent",
        current_stage="stop",
        open_state_token=token,
        snoozed_until=cycle_ts + timedelta(minutes=30),  # активный снуз
    )
    result = CycleResult()
    await _process_one_row(
        None,
        ad_account_id="123",
        account_currency="USD",
        account_currency_exponent=2,
        row=_row(),
        offers=[_offer()],
        states={"9001": snapshot},
        external_deposits={},
        scan_id=1,
        cycle_ts=cycle_ts,
        cabinet_day_start=cycle_ts.replace(hour=0, minute=0, second=0, microsecond=0),
        result=result,
    )

    assert captured["create_called"] is True, "Заснуженный ад при STOP обязан ставить pause-задачу"
    assert captured["metrics_currency"] == "USD"
    assert result.disable_tasks_created == 1
    # Алерт подавлен снузом — apply_fsm получил transition без emit.
    assert captured["apply_transition"].emit_alert is False
    assert result.alerts_stop == 0


# Контроль: НЕ заснуженный ад при STOP и ставит задачу, и эмитит алерт (снуз не влияет).
async def test_not_snoozed_ad_stop_creates_task_and_emits(monkeypatch) -> None:
    cycle_ts = datetime(2026, 7, 2, 12, 0, 0, tzinfo=timezone.utc)
    captured: dict = {}
    _patch_io(monkeypatch, captured=captured)

    snapshot = AdAlertSnapshot(
        ad_id=uuid.uuid4(),
        fb_ad_id="9001",
        alert_state="normal",
        current_stage=None,
        open_state_token=None,
        snoozed_until=None,
    )
    result = CycleResult()
    await _process_one_row(
        None,
        ad_account_id="123",
        account_currency="USD",
        account_currency_exponent=2,
        row=_row(),
        offers=[_offer()],
        states={"9001": snapshot},
        external_deposits={},
        scan_id=1,
        cycle_ts=cycle_ts,
        cabinet_day_start=cycle_ts.replace(hour=0, minute=0, second=0, microsecond=0),
        result=result,
    )

    assert captured["create_called"] is True
    assert captured["metrics_currency"] == "USD"
    assert result.disable_tasks_created == 1
    assert captured["apply_transition"].emit_alert is True  # алерт шлём (не заснужен)
    assert result.alerts_stop == 1


async def test_metrics_persistence_failure_blocks_fsm_and_money_task(monkeypatch) -> None:
    """A row without a durable metric snapshot must never drive auto-pause."""
    cycle_ts = datetime(2026, 7, 2, 12, 0, 0, tzinfo=timezone.utc)
    captured: dict = {}
    _patch_io(monkeypatch, captured=captured)

    monkeypatch.setattr(pl, "insert_metrics", _return_false)

    with pytest.raises(RuntimeError, match="ad_metrics_insert_failed"):
        await _process_one_row(
            None,
            ad_account_id="123",
            account_currency="USD",
            account_currency_exponent=2,
            row=_row(),
            offers=[_offer()],
            states={},
            external_deposits={},
            scan_id=1,
            cycle_ts=cycle_ts,
            cabinet_day_start=cycle_ts.replace(hour=0, minute=0, second=0, microsecond=0),
            result=CycleResult(),
        )

    assert "apply_transition" not in captured
    assert "create_called" not in captured


async def _return_false(*_args, **_kwargs) -> bool:
    return False


# Sanity: RuleEvaluation.stop_rule_codes отдаёт коды хитов — контракт для decide.
def test_ruleeval_stop_codes_contract() -> None:
    ev = _stop_eval()
    assert "cpc_stop" in tuple(ev.stop_rule_codes)


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
