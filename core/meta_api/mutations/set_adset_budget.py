# -*- coding: utf-8 -*-
"""set_adset_budget — изменить бюджет adset.

Graph API: POST /v22.0/{adset_id}?daily_budget=<cents> или
           POST /v22.0/{adset_id}?lifetime_budget=<cents>&end_time=<ISO8601>

Marketing API ожидает бюджет в центах (минимальной валютной единице).
Для lifetime_budget также нужно указать end_time — иначе Meta вернёт ошибку 100.

Пример payload (daily):
    MetaMutationPayload(
        mutation_kind="set_adset_budget",
        target_id="23847000000",  # adset_id
        params={"daily_budget": 5000},  # $50.00 в центах
        ad_account_id="act_123",
    )

Пример payload (lifetime):
    MetaMutationPayload(
        mutation_kind="set_adset_budget",
        target_id="23847000000",
        params={"lifetime_budget": 50000, "end_time": "2026-12-31T23:59:59-0800"},
        ad_account_id="act_123",
    )
"""

from __future__ import annotations

from typing import Any, ClassVar

from core.meta_api.client import MetaApiClient
from core.meta_api.mutations.base import require_numeric_id, success_result
from core.meta_api.schemas import MetaMutationPayload


class SetAdsetBudgetHandler:
    mutation_kind: ClassVar[str] = "set_adset_budget"

    async def execute(
        self,
        client: MetaApiClient,
        payload: MetaMutationPayload,
    ) -> dict[str, Any]:
        adset_id = require_numeric_id(payload.target_id, "target_id (adset_id)")
        params = payload.params or {}

        daily = params.get("daily_budget")
        lifetime = params.get("lifetime_budget")
        end_time = params.get("end_time")

        if (daily is None) == (lifetime is None):
            raise ValueError(
                "set_adset_budget: укажи ровно одно из daily_budget или lifetime_budget"
            )

        graph_params: dict[str, str] = {}
        if daily is not None:
            cents = self._validate_cents(daily, field_name="daily_budget")
            # Graph API требует значения как строки даже для числовых полей.
            graph_params["daily_budget"] = str(cents)
        else:
            cents = self._validate_cents(lifetime, field_name="lifetime_budget")
            graph_params["lifetime_budget"] = str(cents)
            if not end_time or not isinstance(end_time, str):
                raise ValueError(
                    "set_adset_budget: для lifetime_budget обязательно поле end_time "
                    "в формате ISO8601 с timezone offset"
                )
            graph_params["end_time"] = end_time

        graph_response = await client.execute_graph_call(
            method="POST",
            endpoint=f"/{adset_id}",
            query_params=graph_params,
        )
        return success_result(graph_response=graph_response, modified_ids=[adset_id])

    @staticmethod
    def _validate_cents(value: Any, *, field_name: str) -> int:
        """Бюджет — целое положительное число центов. Бросает ValueError на некорректные."""
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(
                f"{field_name}: ожидается целое число центов (int), получено {value!r}"
            )
        if value <= 0:
            raise ValueError(f"{field_name}: должен быть > 0 центов, получено {value}")
        return value
