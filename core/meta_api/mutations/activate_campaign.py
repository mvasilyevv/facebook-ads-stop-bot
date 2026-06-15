# -*- coding: utf-8 -*-
"""activate_campaign — возобновить кампанию.

Graph API: POST /v22.0/{campaign_id}?status=ACTIVE

Если кампания была PAUSED — её adsets/ads, которые были ACTIVE до паузы,
автоматически восстановят свой состояние (Marketing API standard behavior).

Пример payload:
    MetaMutationPayload(
        mutation_kind="activate_campaign",
        target_id="23843000000",
        params={},
        ad_account_id="act_123",
    )
"""

from __future__ import annotations

from typing import Any, ClassVar

from core.meta_api.client import MetaApiClient
from core.meta_api.mutations.base import require_numeric_id, success_result
from core.meta_api.schemas import MetaMutationPayload


class ActivateCampaignHandler:
    mutation_kind: ClassVar[str] = "activate_campaign"

    async def execute(
        self,
        client: MetaApiClient,
        payload: MetaMutationPayload,
    ) -> dict[str, Any]:
        campaign_id = require_numeric_id(payload.target_id, "target_id (campaign_id)")
        graph_response = await client.execute_graph_call(
            ad_account_id=payload.ad_account_id,
            method="POST",
            endpoint=f"/{campaign_id}",
            query_params={"status": "ACTIVE"},
        )
        return success_result(graph_response=graph_response, modified_ids=[campaign_id])
