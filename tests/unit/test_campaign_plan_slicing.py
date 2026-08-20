# -*- coding: utf-8 -*-
"""Одна кампания плана — одна задача, один ключ, один исход (issue #214).

Money-инварианты, которые фиксирует этот файл:

- план из N кампаний разрезается на N самостоятельных конфигов, и каждый несёт
  СВОЙ ключ идемпотентности — повтор плана не создаёт дубль ни одной кампании;
- ключ кампании не зависит от соседних кампаний плана: убрав одну, остальные
  остаются теми же задачами, а не превращаются в новые (иначе повтор плана
  после частичного отказа задваивал бы уже подтверждённые кампании);
- отказ одной кампании не меняет исход остальных: постановка идёт независимо;
- разрез не двигает сквозную нумерацию кодов креативов — коды по-прежнему те
  же, что показало превью всего плана (money-инвариант превью==залив).

Без БД и без сети: разрез и ключ — чистые функции, изоляция постановки
проверяется на инжектированном исполнителе.
"""

from __future__ import annotations

import pytest

from apps.api.routers.v1.campaigns_create import (
    _compute_campaign_idempotency_key,
    _run_launches_independently,
)
from core.campaign_builder.builder import build_campaign_spec, total_code_span
from core.campaign_builder.config import (
    Account,
    AdsetConfig,
    Budget,
    CampaignBlock,
    CampaignConfig,
    Targeting,
)
from core.campaign_builder.plan import campaign_plan_slices


def _block(key: str, *, concepts: int = 2, adsets: int = 2) -> CampaignBlock:
    return CampaignBlock(
        key=key,
        name="{byer} | {offer} | " + key,
        adsets=[
            AdsetConfig(name="{byer} | {offer} | s%d" % index, dir=f"{key}/a{index}", glob="*")
            for index in range(1, adsets + 1)
        ],
        concept_refs=[f"{key}_{index}.jpg" for index in range(1, concepts + 1)],
    )


def _plan(keys: tuple[str, ...], *, account_id: str = "111") -> CampaignConfig:
    return CampaignConfig(
        account=Account(
            act_id=account_id,
            page_id="111",
            pixel_id="222",
            timezone_name="Etc/UTC",
            currency="USD",
            account_context_observed_at="2026-08-15T10:00:00+00:00",
        ),
        offer_code="GH_CR",
        destination_link="https://example.test/click",
        start_date="2099-08-16",
        creo_root="upload-1",
        budget=Budget(currency="USD", daily_amount="10.00", bid_amount="5.00"),
        targeting=Targeting(countries=["GH"]),
        campaigns=[_block(key) for key in keys],
    )


def _keys_by_campaign(config: CampaignConfig) -> dict[str, str]:
    slices = campaign_plan_slices(config)
    return {
        slice_config.campaigns[0].key: _compute_campaign_idempotency_key(slice_config)
        for slice_config in slices
    }


# ---------------------- разрез плана ----------------------


def test_plan_of_four_becomes_four_single_campaign_configs() -> None:
    """Одна кампания плана — один конфиг, и в нём ровно эта кампания."""

    plan = _plan(("c1", "c2", "c3", "c4"))
    slices = campaign_plan_slices(plan)

    assert [slice_config.campaigns[0].key for slice_config in slices] == ["c1", "c2", "c3", "c4"]
    assert all(len(slice_config.campaigns) == 1 for slice_config in slices)


def test_slice_keeps_every_shared_setting_of_the_plan() -> None:
    """Разрез не теряет кабинет, бюджет и таргет — иначе залив уедет не туда."""

    plan = _plan(("c1", "c2"))
    first = campaign_plan_slices(plan)[0]

    assert first.account.act_id == plan.account.act_id
    assert first.budget.daily_amount == plan.budget.daily_amount
    assert first.targeting.countries == plan.targeting.countries
    assert first.creo_root == plan.creo_root
    assert first.offer_code == plan.offer_code
    assert first.start_date == plan.start_date


def test_slicing_a_single_campaign_plan_changes_nothing() -> None:
    """План из одной кампании остаётся ровно одной задачей."""

    plan = _plan(("c1",))
    slices = campaign_plan_slices(plan)

    assert len(slices) == 1
    assert slices[0].campaigns == plan.campaigns


# ---------------------- ключ идемпотентности ----------------------


def test_every_campaign_of_a_plan_gets_its_own_key() -> None:
    """Четыре кампании — четыре разные задачи, а не одна на весь залив."""

    keys = _keys_by_campaign(_plan(("c1", "c2", "c3", "c4")))

    assert len(keys) == 4
    assert len(set(keys.values())) == 4


def test_repeated_launch_of_the_same_plan_reuses_every_campaign_key() -> None:
    """Повторный запуск того же плана не создаёт дубль ни одной кампании."""

    assert _keys_by_campaign(_plan(("c1", "c2", "c3", "c4"))) == _keys_by_campaign(
        _plan(("c1", "c2", "c3", "c4"))
    )


def test_dropping_one_campaign_does_not_move_the_other_campaign_keys() -> None:
    """Ключ кампании не зависит от соседей по плану.

    Это и есть цена разреза, названная владельцем: пока ключ считался по всему
    плану, повтор залива без одной кампании менял ключи ВСЕХ остальных — и
    подтверждённые кампании заливались второй раз.
    """

    full = _keys_by_campaign(_plan(("c1", "c2", "c3", "c4")))
    without_second = _keys_by_campaign(_plan(("c1", "c3", "c4")))

    assert {key: full[key] for key in ("c1", "c3", "c4")} == without_second


def test_campaign_key_is_scoped_by_cabinet() -> None:
    """Одна кампания в двух кабинетах — две задачи, а не одна."""

    first = _keys_by_campaign(_plan(("c1",), account_id="111"))["c1"]
    second = _keys_by_campaign(_plan(("c1",), account_id="222"))["c1"]

    assert first != second


def test_campaign_key_requires_exactly_one_campaign() -> None:
    """Ключ на кампанию нельзя посчитать по целому плану — это молчаливый дубль."""

    with pytest.raises(ValueError):
        _compute_campaign_idempotency_key(_plan(("c1", "c2")))


# ---------------------- независимость исходов ----------------------


@pytest.mark.asyncio
async def test_rejected_campaign_does_not_change_sibling_receipts() -> None:
    """Отказ второй кампании не отменяет постановку первой, третьей и четвёртой."""

    attempted: list[str] = []

    async def launch_one(campaign_key: str) -> str:
        attempted.append(campaign_key)
        if campaign_key == "c2":
            raise ValueError("концепт кампании не найден")
        return f"run-{campaign_key}"

    attempts = await _run_launches_independently(("c1", "c2", "c3", "c4"), launch_one)

    assert attempted == ["c1", "c2", "c3", "c4"]
    assert [(attempt.key, attempt.value) for attempt in attempts if attempt.error is None] == [
        ("c1", "run-c1"),
        ("c3", "run-c3"),
        ("c4", "run-c4"),
    ]
    assert [attempt.key for attempt in attempts if attempt.error is not None] == ["c2"]


# ---------------------- сквозная нумерация кодов ----------------------


def test_per_campaign_spans_reproduce_the_whole_plan_codes() -> None:
    """Разрез не двигает коды креативов: превью всего плана и коды кампаний совпадают.

    Диапазон каждой кампании резервируется отдельно, поэтому смещение следующей
    кампании — это сумма предыдущих. Если бы разрез сбивал нумерацию, sub3=CRxxx
    коллизировал бы между кампаниями одного залива (порча атрибуции трекера).
    """

    plan = _plan(("c1", "c2", "c3", "c4"))
    plan.code_start = 7
    whole_plan = build_campaign_spec(plan)

    code_start = plan.code_start
    for expected_block, slice_config in zip(
        whole_plan.campaigns, campaign_plan_slices(plan), strict=True
    ):
        slice_config.code_start = code_start
        assert build_campaign_spec(slice_config).campaigns == [expected_block]
        code_start += total_code_span(slice_config)

    assert code_start - plan.code_start == total_code_span(plan)
