# -*- coding: utf-8 -*-
"""custom_audience — создание, обновление и удаление Custom Audiences.

Три действия управляются полем params["action"]:
- create  → POST /act_X/customaudiences  (CUSTOM или LOOKALIKE)
- update  → POST /{audience_id}          (name и/или description)
- delete  → DELETE /{audience_id}

Пример payload (create CUSTOM):
    MetaMutationPayload(
        mutation_kind="custom_audience",
        target_id="",          # не используется для create
        ad_account_id="act_42",
        params={
            "action": "create",
            "name": "Buyers Jan",
            "subtype": "CUSTOM",
            "customer_file_source": "USER_PROVIDED_ONLY",
            "description": "Покупатели январь 2026",
        },
    )

Пример payload (create LOOKALIKE):
    MetaMutationPayload(
        mutation_kind="custom_audience",
        target_id="",
        ad_account_id="act_42",
        params={
            "action": "create",
            "name": "LAL Buyers 1%",
            "subtype": "LOOKALIKE",
            "origin_audience_id": "23840000001",
            "country": "US",
            "ratio": 0.01,
        },
    )

Пример payload (update):
    MetaMutationPayload(
        mutation_kind="custom_audience",
        target_id="23840000001",
        params={"action": "update", "name": "Новое имя"},
    )

Пример payload (delete):
    MetaMutationPayload(
        mutation_kind="custom_audience",
        target_id="23840000001",
        params={"action": "delete"},
    )

Ограничения текущей реализации:
- Загрузка CSV пользователей в аудиторию (POST /{id}/users с hashed данными)
  — отдельная фича, здесь не реализована.
- "UploadImage из URL" для thumbnails — тоже вне scope.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar

from core.meta_api.client import MetaApiClient
from core.meta_api.mutations.base import require_numeric_id, success_result
from core.meta_api.schemas import MetaMutationPayload

_VALID_ACTIONS = frozenset({"create", "update", "delete"})
_VALID_SUBTYPES = frozenset({"CUSTOM", "LOOKALIKE"})

_RATIO_MIN = 0.01
_RATIO_MAX = 0.20


class CustomAudienceHandler:
    mutation_kind: ClassVar[str] = "custom_audience"

    async def execute(
        self,
        client: MetaApiClient,
        payload: MetaMutationPayload,
    ) -> dict[str, Any]:
        params = payload.params or {}
        action = params.get("action")

        if action not in _VALID_ACTIONS:
            raise ValueError(
                f"custom_audience: action должен быть одним из {sorted(_VALID_ACTIONS)}, "
                f"получено {action!r}"
            )

        if action == "create":
            return await self._create(client, payload, params)
        elif action == "update":
            return await self._update(client, payload, params)
        else:
            return await self._delete(client, payload)

    # ====================== create ======================

    async def _create(
        self,
        client: MetaApiClient,
        payload: MetaMutationPayload,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        ad_account_id = payload.ad_account_id
        if not ad_account_id or not isinstance(ad_account_id, str):
            raise ValueError("custom_audience create: ad_account_id обязателен (например act_123)")

        name = params.get("name")
        if not name or not isinstance(name, str):
            raise ValueError("custom_audience create: params.name обязателен")

        subtype = params.get("subtype")
        if subtype not in _VALID_SUBTYPES:
            raise ValueError(
                f"custom_audience create: subtype должен быть одним из "
                f"{sorted(_VALID_SUBTYPES)}, получено {subtype!r}"
            )

        body: dict[str, Any] = {"name": name, "subtype": subtype}

        if subtype == "CUSTOM":
            customer_file_source = params.get("customer_file_source")
            if customer_file_source:
                body["customer_file_source"] = customer_file_source
        else:
            # LOOKALIKE — нужен lookalike_spec как JSON-string
            body.update(self._build_lookalike_spec(params))

        description = params.get("description")
        if description:
            body["description"] = description

        graph_response = await client.execute_graph_call(
            method="POST",
            endpoint=f"/{ad_account_id}/customaudiences",
            body_json=body,
        )

        audience_id = str(graph_response.get("id", ""))
        return success_result(
            graph_response=graph_response,
            modified_ids=[audience_id] if audience_id else [],
            extra={"action": "create", "ad_account_id": ad_account_id},
        )

    # ====================== update ======================

    async def _update(
        self,
        client: MetaApiClient,
        payload: MetaMutationPayload,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        audience_id = require_numeric_id(payload.target_id, "target_id (audience_id)")

        body: dict[str, Any] = {}
        if "name" in params:
            body["name"] = params["name"]
        if "description" in params:
            body["description"] = params["description"]

        if not body:
            raise ValueError(
                "custom_audience update: нужно хотя бы одно поле для обновления "
                "(name или description)"
            )

        graph_response = await client.execute_graph_call(
            method="POST",
            endpoint=f"/{audience_id}",
            body_json=body,
        )
        return success_result(
            graph_response=graph_response,
            modified_ids=[audience_id],
            extra={"action": "update"},
        )

    # ====================== delete ======================

    async def _delete(
        self,
        client: MetaApiClient,
        payload: MetaMutationPayload,
    ) -> dict[str, Any]:
        audience_id = require_numeric_id(payload.target_id, "target_id (audience_id)")

        graph_response = await client.execute_graph_call(
            method="DELETE",
            endpoint=f"/{audience_id}",
        )
        return success_result(
            graph_response=graph_response,
            modified_ids=[audience_id],
            extra={"action": "delete"},
        )

    # ====================== helpers ======================

    @staticmethod
    def _build_lookalike_spec(params: dict[str, Any]) -> dict[str, Any]:
        """Собрать lookalike_spec JSON-строку для LOOKALIKE субтипа.

        Meta ожидает поле lookalike_spec как JSON-кодированную строку.
        """
        origin_audience_id = params.get("origin_audience_id")
        if not origin_audience_id:
            raise ValueError("custom_audience LOOKALIKE: origin_audience_id обязателен")
        require_numeric_id(str(origin_audience_id), "origin_audience_id")

        country = params.get("country")
        if not country or not isinstance(country, str):
            raise ValueError("custom_audience LOOKALIKE: country обязателен (например US)")

        ratio = params.get("ratio")
        if ratio is None:
            raise ValueError(
                f"custom_audience LOOKALIKE: ratio обязателен ([{_RATIO_MIN}, {_RATIO_MAX}])"
            )
        try:
            ratio_float = float(ratio)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"custom_audience LOOKALIKE: ratio должен быть числом, получено {ratio!r}"
            ) from exc
        if not (_RATIO_MIN <= ratio_float <= _RATIO_MAX):
            raise ValueError(
                f"custom_audience LOOKALIKE: ratio должен быть в диапазоне "
                f"[{_RATIO_MIN}, {_RATIO_MAX}], получено {ratio_float}"
            )

        spec = {
            "origin_audience_id": str(origin_audience_id),
            "type": "similarity",
            "country": country.upper(),
            "ratio": ratio_float,
        }
        return {"lookalike_spec": json.dumps(spec)}
