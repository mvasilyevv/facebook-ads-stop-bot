# -*- coding: utf-8 -*-
"""pause_ad — поставить объявление на паузу.

Graph API: POST /v22.0/{ad_id}?status=PAUSED

Пример payload:
    MetaMutationPayload(
        mutation_kind="pause_ad",
        target_id="23847238472384",  # ad_id
        params={},                    # params не нужны
        ad_account_id="act_123",
    )
"""

from __future__ import annotations

from typing import Any, ClassVar

from core.meta_api.client import MetaApiClient
from core.meta_api.mutations.base import require_numeric_id, success_result
from core.meta_api.schemas import MetaMutationPayload


class PauseAdHandler:
    mutation_kind: ClassVar[str] = "pause_ad"

    async def execute(
        self,
        client: MetaApiClient,
        payload: MetaMutationPayload,
    ) -> dict[str, Any]:
        ad_id = require_numeric_id(payload.target_id, "target_id (ad_id)")
        graph_response = await client.execute_graph_call(
            method="POST",
            endpoint=f"/{ad_id}",
            query_params={"status": "PAUSED"},
        )
        return success_result(graph_response=graph_response, modified_ids=[ad_id])
