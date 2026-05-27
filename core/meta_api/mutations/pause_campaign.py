# -*- coding: utf-8 -*-
"""pause_campaign — поставить кампанию на паузу.

Graph API: POST /v22.0/{campaign_id}?status=PAUSED

Заметка: пауза campaign автоматически каскадит на adsets и ads — это поведение
Marketing API, мы ему не противодействуем. Если нужно паузить только один
конкретный ad — используй pause_ad.

Пример payload:
    MetaMutationPayload(
        mutation_kind="pause_campaign",
        target_id="23843000000",  # campaign_id
        params={},
        ad_account_id="act_123",
    )
"""

from __future__ import annotations

from typing import Any, ClassVar

from core.meta_api.client import MetaApiClient
from core.meta_api.mutations.base import require_numeric_id, success_result
from core.meta_api.schemas import MetaMutationPayload


class PauseCampaignHandler:
    mutation_kind: ClassVar[str] = "pause_campaign"

    async def execute(
        self,
        client: MetaApiClient,
        payload: MetaMutationPayload,
    ) -> dict[str, Any]:
        campaign_id = require_numeric_id(payload.target_id, "target_id (campaign_id)")
        graph_response = await client.execute_graph_call(
            method="POST",
            endpoint=f"/{campaign_id}",
            query_params={"status": "PAUSED"},
        )
        return success_result(graph_response=graph_response, modified_ids=[campaign_id])
