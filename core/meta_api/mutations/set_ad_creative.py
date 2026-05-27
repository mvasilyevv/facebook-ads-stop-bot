# -*- coding: utf-8 -*-
"""set_ad_creative — заменить creative у существующего объявления.

Graph API: POST /v22.0/{ad_id} body_json={"creative": {"creative_id": "..."}}

Meta ожидает поле creative как вложенный JSON-объект, а не плоский параметр.
Поэтому используем body_json, а не query_params.

Пример payload:
    MetaMutationPayload(
        mutation_kind="set_ad_creative",
        target_id="23847000001",   # ad_id
        params={"creative_id": "23848000001"},
        ad_account_id="act_42",
    )
"""

from __future__ import annotations

from typing import Any, ClassVar

from core.meta_api.client import MetaApiClient
from core.meta_api.mutations.base import require_numeric_id, success_result
from core.meta_api.schemas import MetaMutationPayload


class SetAdCreativeHandler:
    mutation_kind: ClassVar[str] = "set_ad_creative"

    async def execute(
        self,
        client: MetaApiClient,
        payload: MetaMutationPayload,
    ) -> dict[str, Any]:
        ad_id = require_numeric_id(payload.target_id, "target_id (ad_id)")

        params = payload.params or {}
        creative_id_raw = params.get("creative_id")
        if not creative_id_raw:
            raise ValueError("set_ad_creative: params.creative_id обязателен")

        creative_id = require_numeric_id(str(creative_id_raw), "creative_id")

        # Meta ожидает nested объект {"creative": {"creative_id": "..."}}
        body: dict[str, Any] = {"creative": {"creative_id": creative_id}}

        graph_response = await client.execute_graph_call(
            method="POST",
            endpoint=f"/{ad_id}",
            body_json=body,
        )
        return success_result(
            graph_response=graph_response,
            modified_ids=[ad_id],
            extra={"creative_id": creative_id},
        )
