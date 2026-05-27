# -*- coding: utf-8 -*-
"""Unit-тесты для full create_campaign через Graph API Batch.

Проверяем:
- Структура Batch payload: 4 entries (campaign, adset, creative, ad) с правильными
  именами и relative_url.
- JSONPath-ссылки между entries: {result=campaign:$.id} и т.д.
- Извлечение ID из batch response.
- Валидация: пустые секции, image+video, missing creative source, неверные cents и т.д.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import pytest

from core.meta_api.mutations.create_campaign import CreateCampaignHandler
from core.meta_api.schemas import MetaMutationPayload


def _make_client(response: Any = None) -> AsyncMock:
    """Фабрика мок-клиента: execute_graph_call → заданный response."""
    client = AsyncMock()
    client.execute_graph_call = AsyncMock(return_value=response or [])
    return client


def _valid_params() -> dict[str, Any]:
    """Полный валидный payload params для create_campaign."""
    return {
        "campaign": {
            "name": "DRC_CR2 | MV | Tyver | 27.05",
            "objective": "OUTCOME_LEADS",
            "special_ad_categories": ["NONE"],
        },
        "adset": {
            "name": "AdSet 1",
            "daily_budget_cents": 5000,
            "billing_event": "IMPRESSIONS",
            "optimization_goal": "LEAD_GENERATION",
            "targeting": {
                "geo_locations": {"countries": ["RU"]},
                "age_min": 18,
                "age_max": 65,
            },
        },
        "creative": {
            "name": "Creative 1",
            "object_story_spec": {
                "page_id": "123456",
                "link_data": {
                    "message": "Hello",
                    "link": "https://example.com",
                    "image_hash": "abc123hash",
                },
            },
        },
        "ad": {"name": "Ad 1", "status": "PAUSED"},
    }


def _batch_success_response() -> list[dict[str, Any]]:
    """Мок-ответ Batch API: 4 успешных sub-results с ID."""
    return [
        {"code": 200, "body": json.dumps({"id": "23843001"})},
        {"code": 200, "body": json.dumps({"id": "23843002"})},
        {"code": 200, "body": json.dumps({"id": "23843003"})},
        {"code": 200, "body": json.dumps({"id": "23843004"})},
    ]


# Валидный payload собирает Batch с 4 entries и нужными именами.
@pytest.mark.asyncio
async def test_create_campaign_full_builds_4_entry_batch() -> None:
    client = _make_client(_batch_success_response())
    payload = MetaMutationPayload(
        mutation_kind="create_campaign",
        target_id="new",
        params=_valid_params(),
        ad_account_id="act_999",
    )

    result = await CreateCampaignHandler().execute(client, payload)

    client.execute_graph_call.assert_awaited_once()
    kwargs = client.execute_graph_call.call_args.kwargs
    assert kwargs["method"] == "POST"
    assert kwargs["endpoint"] == "/"
    batch = json.loads(kwargs["query_params"]["batch"])
    assert len(batch) == 4
    assert [e["name"] for e in batch] == ["campaign", "adset", "creative", "ad"]
    assert all(e["method"] == "POST" for e in batch)
    assert batch[0]["relative_url"] == "act_999/campaigns"
    assert batch[1]["relative_url"] == "act_999/adsets"
    assert batch[2]["relative_url"] == "act_999/adcreatives"
    assert batch[3]["relative_url"] == "act_999/ads"

    assert result["success"] is True
    assert result["modified_ids"] == ["23843001", "23843002", "23843003", "23843004"]
    assert result["campaign_id"] == "23843001"
    assert result["adset_id"] == "23843002"
    assert result["creative_id"] == "23843003"
    assert result["ad_id"] == "23843004"


# AdSet body содержит {result=campaign:$.id} ссылку для campaign_id.
@pytest.mark.asyncio
async def test_create_campaign_uses_jsonpath_ref_for_adset() -> None:
    client = _make_client(_batch_success_response())
    payload = MetaMutationPayload(
        mutation_kind="create_campaign",
        target_id="new",
        params=_valid_params(),
        ad_account_id="act_999",
    )

    await CreateCampaignHandler().execute(client, payload)

    batch = json.loads(client.execute_graph_call.call_args.kwargs["query_params"]["batch"])
    adset_body = batch[1]["body"]
    # form-encoded body: campaign_id=%7Bresult%3Dcampaign%3A%24.id%7D
    # decode чтобы проверить
    from urllib.parse import parse_qs

    parsed = parse_qs(adset_body)
    assert parsed["campaign_id"] == ["{result=campaign:$.id}"]


# Ad body ссылается на adset и creative через JSONPath.
@pytest.mark.asyncio
async def test_create_campaign_ad_references_adset_and_creative() -> None:
    client = _make_client(_batch_success_response())
    payload = MetaMutationPayload(
        mutation_kind="create_campaign",
        target_id="new",
        params=_valid_params(),
        ad_account_id="act_999",
    )

    await CreateCampaignHandler().execute(client, payload)

    batch = json.loads(client.execute_graph_call.call_args.kwargs["query_params"]["batch"])
    ad_body = batch[3]["body"]
    from urllib.parse import parse_qs

    parsed = parse_qs(ad_body)
    assert parsed["adset_id"] == ["{result=adset:$.id}"]
    # creative — JSON-encoded dict с creative_id
    creative_field = parsed["creative"][0]
    creative_obj = json.loads(creative_field)
    assert creative_obj == {"creative_id": "{result=creative:$.id}"}


# Campaign body содержит objective, status, special_ad_categories.
@pytest.mark.asyncio
async def test_create_campaign_campaign_body_fields() -> None:
    client = _make_client(_batch_success_response())
    params = _valid_params()
    params["campaign"]["status_after_create"] = "ACTIVE"
    payload = MetaMutationPayload(
        mutation_kind="create_campaign",
        target_id="new",
        params=params,
        ad_account_id="act_999",
    )

    await CreateCampaignHandler().execute(client, payload)

    batch = json.loads(client.execute_graph_call.call_args.kwargs["query_params"]["batch"])
    from urllib.parse import parse_qs

    campaign_body = parse_qs(batch[0]["body"])
    assert campaign_body["name"] == ["DRC_CR2 | MV | Tyver | 27.05"]
    assert campaign_body["objective"] == ["OUTCOME_LEADS"]
    assert campaign_body["status"] == ["ACTIVE"]
    assert json.loads(campaign_body["special_ad_categories"][0]) == ["NONE"]


# Top-level image_hash и video_id одновременно — ValueError.
@pytest.mark.asyncio
async def test_create_campaign_rejects_image_and_video() -> None:
    client = _make_client()
    params = _valid_params()
    params["creative"]["image_hash"] = "abc"
    params["creative"]["video_id"] = "123"
    payload = MetaMutationPayload(
        mutation_kind="create_campaign",
        target_id="new",
        params=params,
        ad_account_id="act_999",
    )

    with pytest.raises(ValueError, match="mutually exclusive"):
        await CreateCampaignHandler().execute(client, payload)

    client.execute_graph_call.assert_not_awaited()


# object_story_spec без link_data/video_data/image_hash — ValueError.
@pytest.mark.asyncio
async def test_create_campaign_rejects_empty_creative_source() -> None:
    client = _make_client()
    params = _valid_params()
    params["creative"]["object_story_spec"] = {"page_id": "123"}  # без link_data
    payload = MetaMutationPayload(
        mutation_kind="create_campaign",
        target_id="new",
        params=params,
        ad_account_id="act_999",
    )

    with pytest.raises(ValueError, match="link_data/video_data"):
        await CreateCampaignHandler().execute(client, payload)


# ad_account_id без префикса act_ — ValueError.
@pytest.mark.asyncio
async def test_create_campaign_rejects_missing_act_prefix() -> None:
    client = _make_client()
    payload = MetaMutationPayload(
        mutation_kind="create_campaign",
        target_id="new",
        params=_valid_params(),
        ad_account_id="999",
    )

    with pytest.raises(ValueError, match="act_"):
        await CreateCampaignHandler().execute(client, payload)


# Отсутствует секция creative — ValueError на валидации.
@pytest.mark.asyncio
async def test_create_campaign_rejects_missing_creative_section() -> None:
    client = _make_client()
    params = _valid_params()
    del params["creative"]
    payload = MetaMutationPayload(
        mutation_kind="create_campaign",
        target_id="new",
        params=params,
        ad_account_id="act_999",
    )

    with pytest.raises(ValueError, match="секция 'creative' обязательна"):
        await CreateCampaignHandler().execute(client, payload)


# Неизвестный objective отвергается.
@pytest.mark.asyncio
async def test_create_campaign_rejects_unknown_objective() -> None:
    client = _make_client()
    params = _valid_params()
    params["campaign"]["objective"] = "OUTCOME_NONSENSE"
    payload = MetaMutationPayload(
        mutation_kind="create_campaign",
        target_id="new",
        params=params,
        ad_account_id="act_999",
    )

    with pytest.raises(ValueError, match="objective"):
        await CreateCampaignHandler().execute(client, payload)


# AdSet без бюджета — ValueError.
@pytest.mark.asyncio
async def test_create_campaign_rejects_adset_without_budget() -> None:
    client = _make_client()
    params = _valid_params()
    del params["adset"]["daily_budget_cents"]
    payload = MetaMutationPayload(
        mutation_kind="create_campaign",
        target_id="new",
        params=params,
        ad_account_id="act_999",
    )

    with pytest.raises(ValueError, match="daily_budget_cents или lifetime_budget_cents"):
        await CreateCampaignHandler().execute(client, payload)


# AdSet с двумя бюджетами одновременно — ValueError.
@pytest.mark.asyncio
async def test_create_campaign_rejects_adset_both_budgets() -> None:
    client = _make_client()
    params = _valid_params()
    params["adset"]["lifetime_budget_cents"] = 10000
    payload = MetaMutationPayload(
        mutation_kind="create_campaign",
        target_id="new",
        params=params,
        ad_account_id="act_999",
    )

    with pytest.raises(ValueError, match="не больше одного"):
        await CreateCampaignHandler().execute(client, payload)


# Targeting без geo_locations — ValueError (Meta откажется создать adset).
@pytest.mark.asyncio
async def test_create_campaign_rejects_targeting_without_geo() -> None:
    client = _make_client()
    params = _valid_params()
    params["adset"]["targeting"] = {"age_min": 18, "age_max": 65}
    payload = MetaMutationPayload(
        mutation_kind="create_campaign",
        target_id="new",
        params=params,
        ad_account_id="act_999",
    )

    with pytest.raises(ValueError, match="geo_locations"):
        await CreateCampaignHandler().execute(client, payload)


# Bid_amount опционально, если задан — попадает в adset body.
@pytest.mark.asyncio
async def test_create_campaign_adset_bid_amount_optional() -> None:
    client = _make_client(_batch_success_response())
    params = _valid_params()
    params["adset"]["bid_amount_cents"] = 250
    payload = MetaMutationPayload(
        mutation_kind="create_campaign",
        target_id="new",
        params=params,
        ad_account_id="act_999",
    )

    await CreateCampaignHandler().execute(client, payload)

    batch = json.loads(client.execute_graph_call.call_args.kwargs["query_params"]["batch"])
    from urllib.parse import parse_qs

    adset_body = parse_qs(batch[1]["body"])
    assert adset_body["bid_amount"] == ["250"]


# Если один из sub-results не успешен — handler бросает ValueError с деталями.
@pytest.mark.asyncio
async def test_create_campaign_raises_when_subrequest_fails() -> None:
    failure_response = [
        {"code": 200, "body": json.dumps({"id": "23843001"})},
        {"code": 400, "body": json.dumps({"error": {"message": "Invalid targeting"}})},
        {"code": 200, "body": json.dumps({"id": "23843003"})},
        {"code": 200, "body": json.dumps({"id": "23843004"})},
    ]
    client = _make_client(failure_response)
    payload = MetaMutationPayload(
        mutation_kind="create_campaign",
        target_id="new",
        params=_valid_params(),
        ad_account_id="act_999",
    )

    with pytest.raises(ValueError, match="не полностью успешен"):
        await CreateCampaignHandler().execute(client, payload)


# Campaign CBO бюджет с daily_budget_cents — попадает как daily_budget в body.
@pytest.mark.asyncio
async def test_create_campaign_campaign_cbo_daily_budget() -> None:
    client = _make_client(_batch_success_response())
    params = _valid_params()
    params["campaign"]["daily_budget_cents"] = 10000
    payload = MetaMutationPayload(
        mutation_kind="create_campaign",
        target_id="new",
        params=params,
        ad_account_id="act_999",
    )

    await CreateCampaignHandler().execute(client, payload)

    batch = json.loads(client.execute_graph_call.call_args.kwargs["query_params"]["batch"])
    from urllib.parse import parse_qs

    campaign_body = parse_qs(batch[0]["body"])
    assert campaign_body["daily_budget"] == ["10000"]


# Top-level image_hash прокидывается в creative body.
@pytest.mark.asyncio
async def test_create_campaign_top_level_image_hash_in_body() -> None:
    client = _make_client(_batch_success_response())
    params = _valid_params()
    # Заменим object_story_spec на минимальный без link_data, но добавим top-level image_hash.
    params["creative"]["object_story_spec"] = {"page_id": "123"}
    params["creative"]["image_hash"] = "abc123hash"
    payload = MetaMutationPayload(
        mutation_kind="create_campaign",
        target_id="new",
        params=params,
        ad_account_id="act_999",
    )

    await CreateCampaignHandler().execute(client, payload)

    batch = json.loads(client.execute_graph_call.call_args.kwargs["query_params"]["batch"])
    from urllib.parse import parse_qs

    creative_body = parse_qs(batch[2]["body"])
    assert creative_body["image_hash"] == ["abc123hash"]


# Top-level video_id прокидывается в creative body.
@pytest.mark.asyncio
async def test_create_campaign_top_level_video_id_in_body() -> None:
    client = _make_client(_batch_success_response())
    params = _valid_params()
    params["creative"]["object_story_spec"] = {"page_id": "123"}
    params["creative"]["video_id"] = "video_abc"
    payload = MetaMutationPayload(
        mutation_kind="create_campaign",
        target_id="new",
        params=params,
        ad_account_id="act_999",
    )

    await CreateCampaignHandler().execute(client, payload)

    batch = json.loads(client.execute_graph_call.call_args.kwargs["query_params"]["batch"])
    from urllib.parse import parse_qs

    creative_body = parse_qs(batch[2]["body"])
    assert creative_body["video_id"] == ["video_abc"]
