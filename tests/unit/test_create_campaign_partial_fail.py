# -*- coding: utf-8 -*-
"""Unit-тесты CreateCampaignPartialError и поведение partial fail.

Проверяем:
1. Частичный успех batch → CreateCampaignPartialError с created_ids (не теряем id).
2. created_ids содержит только реально созданные id (не все).
3. failed_steps содержит упавшие шаги с кодом ошибки.
4. Полный успех → успешный result (регрессия).
5. Только первый шаг упал → created_ids пустой.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import pytest

from core.meta_api.mutations.create_campaign import (
    CreateCampaignHandler,
    CreateCampaignPartialError,
)
from core.meta_api.schemas import MetaMutationPayload


def _make_client(response: Any = None) -> AsyncMock:
    """Фабрика мок-клиента."""
    client = AsyncMock()
    client.execute_graph_call = AsyncMock(return_value=response or [])
    return client


def _valid_params() -> dict[str, Any]:
    """Валидный payload params."""
    return {
        "campaign": {
            "name": "DRC_CR2 | Test",
            "objective": "OUTCOME_LEADS",
            "special_ad_categories": ["NONE"],
        },
        "adset": {
            "name": "AdSet Test",
            "daily_budget_cents": 5000,
            "billing_event": "IMPRESSIONS",
            "optimization_goal": "LEAD_GENERATION",
            "targeting": {"geo_locations": {"countries": ["RU"]}, "age_min": 18, "age_max": 65},
        },
        "creative": {
            "name": "Creative Test",
            "object_story_spec": {
                "page_id": "123456",
                "link_data": {
                    "message": "Hello",
                    "link": "https://example.com",
                    "image_hash": "abc",
                },
            },
        },
        "ad": {"name": "Ad Test", "status": "PAUSED"},
    }


# Если 4-й sub-request (ad) упал — campaign/adset/creative созданы и должны быть в created_ids
@pytest.mark.asyncio
async def test_partial_fail_last_step_exposes_created_ids() -> None:
    """Batch: campaign+adset+creative созданы, ad упал → created_ids содержит эти 3 id."""
    partial_response = [
        {"code": 200, "body": json.dumps({"id": "23843001"})},
        {"code": 200, "body": json.dumps({"id": "23843002"})},
        {"code": 200, "body": json.dumps({"id": "23843003"})},
        {
            "code": 400,
            "body": json.dumps({"error": {"message": "Ad creation failed", "code": 100}}),
        },
    ]
    client = _make_client(partial_response)
    payload = MetaMutationPayload(
        mutation_kind="create_campaign",
        target_id="new",
        params=_valid_params(),
        ad_account_id="act_999",
    )

    # Должна быть структурированная ошибка, а не просто ValueError
    with pytest.raises(CreateCampaignPartialError) as exc_info:
        await CreateCampaignHandler().execute(client, payload)

    exc = exc_info.value
    # Все 3 созданных id доступны структурно (не теряются в строке ошибки)
    assert exc.created_ids["campaign_id"] == "23843001"
    assert exc.created_ids["adset_id"] == "23843002"
    assert exc.created_ids["creative_id"] == "23843003"
    # ad не создан → нет в created_ids
    assert "ad_id" not in exc.created_ids

    # failed_steps содержит информацию о упавшем шаге
    assert len(exc.failed_steps) == 1
    assert exc.failed_steps[0]["step"] == "ad"
    assert exc.failed_steps[0]["code"] == 400


# Если упал первый шаг (campaign) → created_ids пустой
@pytest.mark.asyncio
async def test_partial_fail_first_step_creates_ids_empty() -> None:
    """Batch: campaign упал сразу → created_ids пустой (нет осиротевших объектов)."""
    response = [
        {"code": 500, "body": json.dumps({"error": {"message": "Internal error", "code": 1}})},
        {"code": 400, "body": json.dumps({"error": {"message": "Missing campaign_id"}})},
        {"code": 400, "body": json.dumps({"error": {"message": "Missing campaign_id"}})},
        {"code": 400, "body": json.dumps({"error": {"message": "Missing campaign_id"}})},
    ]
    client = _make_client(response)
    payload = MetaMutationPayload(
        mutation_kind="create_campaign",
        target_id="new",
        params=_valid_params(),
        ad_account_id="act_999",
    )

    with pytest.raises(CreateCampaignPartialError) as exc_info:
        await CreateCampaignHandler().execute(client, payload)

    exc = exc_info.value
    # Ни один объект не создан → created_ids пустой
    assert exc.created_ids == {}
    assert len(exc.failed_steps) == 4


# Если средний шаг (adset) упал — campaign создан, adset/creative/ad нет
@pytest.mark.asyncio
async def test_partial_fail_middle_step_only_campaign_created() -> None:
    """Batch: adset упал → только campaign_id в created_ids."""
    response = [
        {"code": 200, "body": json.dumps({"id": "23843001"})},
        {"code": 400, "body": json.dumps({"error": {"message": "Invalid targeting", "code": 100}})},
        {"code": 200, "body": json.dumps({"id": "23843003"})},
        {"code": 200, "body": json.dumps({"id": "23843004"})},
    ]
    client = _make_client(response)
    payload = MetaMutationPayload(
        mutation_kind="create_campaign",
        target_id="new",
        params=_valid_params(),
        ad_account_id="act_999",
    )

    with pytest.raises(CreateCampaignPartialError) as exc_info:
        await CreateCampaignHandler().execute(client, payload)

    exc = exc_info.value
    # Только campaign создан (несмотря на то что creative/ad тоже вернули 200)
    assert exc.created_ids.get("campaign_id") == "23843001"
    # adset упал → нет в created_ids
    assert "adset_id" not in exc.created_ids
    # creative и ad создались (sub-result 200), но без adset они осиротевшие
    assert exc.failed_steps[0]["step"] == "adset"


# Регрессия: полный успех → обычный result, не Exception
@pytest.mark.asyncio
async def test_full_success_no_exception() -> None:
    """Все 4 sub-requests успешны → возвращает result без Exception."""
    success_response = [
        {"code": 200, "body": json.dumps({"id": "23843001"})},
        {"code": 200, "body": json.dumps({"id": "23843002"})},
        {"code": 200, "body": json.dumps({"id": "23843003"})},
        {"code": 200, "body": json.dumps({"id": "23843004"})},
    ]
    client = _make_client(success_response)
    payload = MetaMutationPayload(
        mutation_kind="create_campaign",
        target_id="new",
        params=_valid_params(),
        ad_account_id="act_999",
    )

    result = await CreateCampaignHandler().execute(client, payload)

    assert result["success"] is True
    assert result["campaign_id"] == "23843001"
    assert result["ad_id"] == "23843004"


# CreateCampaignPartialError является Exception, но НЕ MutationValidationError
def test_partial_error_is_not_mutation_validation_error() -> None:
    """CreateCampaignPartialError — не MutationValidationError (разные ветки в worker'е)."""
    from core.meta_api.errors import MutationValidationError

    exc = CreateCampaignPartialError(
        "test",
        created_ids={"campaign_id": "111"},
        failed_steps=[{"step": "ad", "code": 400, "error": "fail"}],
    )
    assert isinstance(exc, Exception)
    assert not isinstance(exc, MutationValidationError)
    assert not isinstance(exc, ValueError)


# ─── Аудит 2026-07-12 (M-2): ничего не создано + все провалы транзиентные → retry ──


# Все sub-requests упали ЯВНЫМ rate-limit (code 613, ничего не создано) →
# NothingCommittedError (worker: requeue даже для irreversible), а не
# CreateCampaignPartialError → «залив навсегда умер». Голый Temporary не годится —
# worker уводит его в _fail_irreversible.
@pytest.mark.asyncio
async def test_nothing_created_all_transient_raises_temporary() -> None:
    from core.meta_api.errors import NothingCommittedError

    rate_limited = json.dumps({"error": {"message": "limit reached", "code": 613}})
    response = [
        {"code": 400, "body": rate_limited},
        {"code": 400, "body": rate_limited},
        {"code": 400, "body": rate_limited},
        {"code": 400, "body": rate_limited},
    ]
    client = _make_client(response)
    payload = MetaMutationPayload(
        mutation_kind="create_campaign",
        target_id="new",
        params=_valid_params(),
        ad_account_id="act_999",
    )

    with pytest.raises(NothingCommittedError):
        await CreateCampaignHandler().execute(client, payload)


# null-саб (timeout, code 0) среди провалов → операция могла выполниться без ответа →
# остаётся PartialError (fail_irreversible), retry НЕ форсируется.
@pytest.mark.asyncio
async def test_nothing_created_with_null_sub_keeps_partial_error() -> None:
    rate_limited = json.dumps({"error": {"message": "limit reached", "code": 613}})
    response = [
        None,  # timeout — кампания МОГЛА создаться
        {"code": 400, "body": rate_limited},
        {"code": 400, "body": rate_limited},
        {"code": 400, "body": rate_limited},
    ]
    client = _make_client(response)
    payload = MetaMutationPayload(
        mutation_kind="create_campaign",
        target_id="new",
        params=_valid_params(),
        ad_account_id="act_999",
    )

    with pytest.raises(CreateCampaignPartialError):
        await CreateCampaignHandler().execute(client, payload)


# Ничего не создано, но провалы PERMANENT (code 100) → прежний PartialError→fail.
@pytest.mark.asyncio
async def test_nothing_created_permanent_keeps_partial_error() -> None:
    permanent = json.dumps({"error": {"message": "Invalid parameter", "code": 100}})
    response = [{"code": 400, "body": permanent} for _ in range(4)]
    client = _make_client(response)
    payload = MetaMutationPayload(
        mutation_kind="create_campaign",
        target_id="new",
        params=_valid_params(),
        ad_account_id="act_999",
    )

    with pytest.raises(CreateCampaignPartialError):
        await CreateCampaignHandler().execute(client, payload)
