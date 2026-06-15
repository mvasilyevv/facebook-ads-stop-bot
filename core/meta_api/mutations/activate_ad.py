# -*- coding: utf-8 -*-
"""activate_ad — возобновить объявление.

Graph API: POST /v22.0/{ad_id}?status=ACTIVE

Пример payload:
    MetaMutationPayload(
        mutation_kind="activate_ad",
        target_id="23847238472384",
        params={},
        ad_account_id="act_123",
    )
"""

from __future__ import annotations

from typing import Any, ClassVar

from core.meta_api.client import MetaApiClient
from core.meta_api.mutations.base import require_numeric_id, success_result
from core.meta_api.schemas import MetaMutationPayload


class ActivateAdHandler:
    mutation_kind: ClassVar[str] = "activate_ad"

    async def execute(
        self,
        client: MetaApiClient,
        payload: MetaMutationPayload,
    ) -> dict[str, Any]:
        ad_id = require_numeric_id(payload.target_id, "target_id (ad_id)")
        graph_response = await client.execute_graph_call(
            ad_account_id=payload.ad_account_id,
            method="POST",
            endpoint=f"/{ad_id}",
            query_params={"status": "ACTIVE"},
        )
        return success_result(graph_response=graph_response, modified_ids=[ad_id])
