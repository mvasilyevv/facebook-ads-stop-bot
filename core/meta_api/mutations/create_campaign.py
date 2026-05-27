# -*- coding: utf-8 -*-
"""create_campaign — полное создание иерархии Campaign + AdSet + AdCreative + Ad.

Использует Graph API Batch API: один POST к корню (/) с параметром
batch=[entry1, entry2, ...]. Sub-requests ссылаются друг на друга через
JSONPath ({result=campaign:$.id}), Meta сама подставит реальные ID в момент
исполнения сабжей.

Иерархия Marketing API:
    Campaign (objective, status, special_ad_categories, [budget])
       └── AdSet (campaign_id, targeting, optimization_goal, billing_event, budget, schedule)
              └── Ad (adset_id, creative_id, status)
                     └── AdCreative (object_story_spec с image_hash или video_id)

В нашем варианте AdCreative создаётся ОТДЕЛЬНЫМ entry в том же batch'е и
ссылается из Ad через JSONPath.

ОЖИДАЕМАЯ ИЕРАРХИЯ PAYLOAD:
    {
        "campaign": {
            "name": "...",
            "objective": "OUTCOME_LEADS",
            "status_after_create": "PAUSED",
            "special_ad_categories": ["NONE"],
            "daily_budget_cents": 5000   # ОПЦИОНАЛЬНО (CBO)
        },
        "adset": {
            "name": "...",
            "daily_budget_cents": 5000,  # либо это, либо lifetime_budget_cents
            "lifetime_budget_cents": ...,
            "billing_event": "IMPRESSIONS",
            "optimization_goal": "LEAD_GENERATION",
            "bid_amount_cents": 200,     # опционально
            "targeting": {...},           # JSON targeting spec
            "start_time": "2026-05-27T12:00:00+0000",  # опционально
            "end_time": "...",            # опционально
            "promoted_object": {...}      # для conversion objectives
        },
        "creative": {
            "name": "...",
            "object_story_spec": {...},   # обязательно
            "image_hash": "abc..." | "video_id": "123..." # mutually exclusive
        },
        "ad": {
            "name": "...",
            "status": "PAUSED"
        }
    }

Пример:
    MetaMutationPayload(
        mutation_kind="create_campaign",
        target_id="new",
        params={
            "campaign": {
                "name": "DRC_CR2 | MV | 27.05",
                "objective": "OUTCOME_LEADS",
                "special_ad_categories": ["NONE"],
            },
            "adset": {
                "name": "AdSet 1",
                "daily_budget_cents": 5000,
                "billing_event": "IMPRESSIONS",
                "optimization_goal": "LEAD_GENERATION",
                "targeting": {"geo_locations": {"countries": ["RU"]}, "age_min": 18, "age_max": 65},
            },
            "creative": {
                "name": "Creative 1",
                "object_story_spec": {
                    "page_id": "123456",
                    "link_data": {"message": "...", "link": "...", "image_hash": "abc"},
                },
            },
            "ad": {"name": "Ad 1", "status": "PAUSED"},
        },
        ad_account_id="act_123456",
    )
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from core.meta_api.client import MetaApiClient
from core.meta_api.mutations._batch_helpers import (
    build_batch_payload,
    jsonpath_ref,
    make_batch_entry,
    parse_batch_response,
)
from core.meta_api.mutations.base import success_result
from core.meta_api.schemas import MetaMutationPayload

logger = logging.getLogger(__name__)

_VALID_OBJECTIVES = frozenset(
    {
        "OUTCOME_LEADS",
        "OUTCOME_SALES",
        "OUTCOME_TRAFFIC",
        "OUTCOME_AWARENESS",
        "OUTCOME_ENGAGEMENT",
        "OUTCOME_APP_PROMOTION",
    }
)

_VALID_SPECIAL_CATEGORIES = frozenset(
    {
        "NONE",
        "EMPLOYMENT",
        "HOUSING",
        "CREDIT",
        "ISSUES_ELECTIONS_POLITICS",
    }
)

_VALID_BILLING_EVENTS = frozenset(
    {
        "IMPRESSIONS",
        "LINK_CLICKS",
        "POST_ENGAGEMENT",
        "VIDEO_VIEWS",
        "THRUPLAY",
    }
)


class CreateCampaignHandler:
    mutation_kind: ClassVar[str] = "create_campaign"

    async def execute(
        self,
        client: MetaApiClient,
        payload: MetaMutationPayload,
    ) -> dict[str, Any]:
        ad_account_id = self._validate_ad_account(payload.ad_account_id)
        params = payload.params or {}

        campaign_spec = self._validate_section(params, "campaign")
        adset_spec = self._validate_section(params, "adset")
        creative_spec = self._validate_section(params, "creative")
        ad_spec = self._validate_section(params, "ad")

        campaign_body = self._build_campaign_body(campaign_spec)
        adset_body = self._build_adset_body(adset_spec, campaign_ref=jsonpath_ref("campaign"))
        creative_body = self._build_creative_body(creative_spec)
        ad_body = self._build_ad_body(
            ad_spec,
            adset_ref=jsonpath_ref("adset"),
            creative_ref=jsonpath_ref("creative"),
        )

        entries = [
            make_batch_entry(
                method="POST",
                relative_url=f"{ad_account_id}/campaigns",
                body_params=campaign_body,
                name="campaign",
            ),
            make_batch_entry(
                method="POST",
                relative_url=f"{ad_account_id}/adsets",
                body_params=adset_body,
                name="adset",
            ),
            make_batch_entry(
                method="POST",
                relative_url=f"{ad_account_id}/adcreatives",
                body_params=creative_body,
                name="creative",
            ),
            make_batch_entry(
                method="POST",
                relative_url=f"{ad_account_id}/ads",
                body_params=ad_body,
                name="ad",
            ),
        ]

        batch_json = build_batch_payload(entries)

        graph_response = await client.execute_graph_call(
            method="POST",
            endpoint="/",
            query_params={"batch": batch_json},
        )

        sub_results = parse_batch_response(graph_response, expected_count=len(entries))

        # Извлекаем ID из каждого sub_result. Порядок entries фиксирован.
        ids = self._extract_ids(sub_results)

        # Если хоть один sub-request не успешен — отдаём агрегированную ошибку.
        if not all(r["success"] for r in sub_results):
            failed = [
                {
                    "step": ("campaign", "adset", "creative", "ad")[r["index"]],
                    "code": r["code"],
                    "error": r.get("error"),
                }
                for r in sub_results
                if not r["success"]
            ]
            raise ValueError(
                f"create_campaign: batch не полностью успешен: {failed} (ids_so_far={ids})"
            )

        modified_ids = [v for v in ids.values() if v]
        return success_result(
            graph_response=graph_response,
            modified_ids=modified_ids,
            extra={
                "ad_account_id": ad_account_id,
                "campaign_id": ids.get("campaign_id"),
                "adset_id": ids.get("adset_id"),
                "creative_id": ids.get("creative_id"),
                "ad_id": ids.get("ad_id"),
                "campaign_name": campaign_spec.get("name"),
                "objective": campaign_body.get("objective"),
            },
        )

    # ====================== Builders ======================

    @staticmethod
    def _validate_ad_account(value: str | None) -> str:
        if not value or not isinstance(value, str):
            raise ValueError("ad_account_id обязателен")
        cleaned = value.strip()
        if not cleaned.startswith("act_"):
            raise ValueError(f"ad_account_id должен начинаться с 'act_', получено {cleaned!r}")
        return cleaned

    @staticmethod
    def _validate_section(params: dict[str, Any], key: str) -> dict[str, Any]:
        section = params.get(key)
        if section is None:
            raise ValueError(f"create_campaign: секция {key!r} обязательна в params")
        if not isinstance(section, dict):
            raise ValueError(
                f"create_campaign: секция {key!r} должна быть dict, получено {type(section).__name__}"
            )
        return section

    @classmethod
    def _build_campaign_body(cls, spec: dict[str, Any]) -> dict[str, Any]:
        name = cls._validate_name(spec.get("name"), section="campaign")
        objective = cls._validate_objective(spec.get("objective"))
        status = cls._validate_status(spec.get("status_after_create", "PAUSED"), section="campaign")
        special = cls._validate_special_categories(spec.get("special_ad_categories", ["NONE"]))

        body: dict[str, Any] = {
            "name": name,
            "objective": objective,
            "status": status,
            "special_ad_categories": special,
        }

        daily = spec.get("daily_budget_cents")
        lifetime = spec.get("lifetime_budget_cents")
        if daily is not None and lifetime is not None:
            raise ValueError(
                "campaign: укажи не больше одного из daily_budget_cents/lifetime_budget_cents"
            )
        if daily is not None:
            body["daily_budget"] = cls._validate_cents(
                daily, field_name="campaign.daily_budget_cents"
            )
        elif lifetime is not None:
            body["lifetime_budget"] = cls._validate_cents(
                lifetime, field_name="campaign.lifetime_budget_cents"
            )

        return body

    @classmethod
    def _build_adset_body(cls, spec: dict[str, Any], *, campaign_ref: str) -> dict[str, Any]:
        name = cls._validate_name(spec.get("name"), section="adset")
        billing = cls._validate_billing_event(spec.get("billing_event"))
        optimization_goal = cls._validate_optimization_goal(spec.get("optimization_goal"))
        targeting = cls._validate_targeting(spec.get("targeting"))
        status = cls._validate_status(spec.get("status_after_create", "PAUSED"), section="adset")

        body: dict[str, Any] = {
            "name": name,
            "campaign_id": campaign_ref,
            "billing_event": billing,
            "optimization_goal": optimization_goal,
            "targeting": targeting,
            "status": status,
        }

        daily = spec.get("daily_budget_cents")
        lifetime = spec.get("lifetime_budget_cents")
        if daily is None and lifetime is None:
            raise ValueError("adset: укажи daily_budget_cents или lifetime_budget_cents")
        if daily is not None and lifetime is not None:
            raise ValueError(
                "adset: укажи не больше одного из daily_budget_cents/lifetime_budget_cents"
            )
        if daily is not None:
            body["daily_budget"] = cls._validate_cents(daily, field_name="adset.daily_budget_cents")
        if lifetime is not None:
            body["lifetime_budget"] = cls._validate_cents(
                lifetime, field_name="adset.lifetime_budget_cents"
            )

        bid_amount = spec.get("bid_amount_cents")
        if bid_amount is not None:
            body["bid_amount"] = cls._validate_cents(
                bid_amount, field_name="adset.bid_amount_cents"
            )

        for key in ("start_time", "end_time"):
            value = spec.get(key)
            if value is not None:
                if not isinstance(value, str):
                    raise ValueError(f"adset.{key}: ожидается строка ISO-8601, получено {value!r}")
                body[key] = value

        promoted = spec.get("promoted_object")
        if promoted is not None:
            if not isinstance(promoted, dict):
                raise ValueError(
                    f"adset.promoted_object: ожидается dict, получено {type(promoted).__name__}"
                )
            body["promoted_object"] = promoted

        return body

    @classmethod
    def _build_creative_body(cls, spec: dict[str, Any]) -> dict[str, Any]:
        name = cls._validate_name(spec.get("name"), section="creative")
        object_story_spec = spec.get("object_story_spec")
        if not isinstance(object_story_spec, dict) or not object_story_spec:
            raise ValueError("creative.object_story_spec: обязателен и должен быть непустым dict")

        body: dict[str, Any] = {
            "name": name,
            "object_story_spec": object_story_spec,
        }

        # image_hash и video_id mutually exclusive на уровне creative;
        # они обычно живут внутри object_story_spec.link_data/video_data,
        # но допускаем также top-level (тогда подсунем в object_story_spec).
        image_hash = spec.get("image_hash")
        video_id = spec.get("video_id")
        if image_hash and video_id:
            raise ValueError(
                "creative: image_hash и video_id mutually exclusive — задай одно из двух"
            )
        # Валидация: внутри object_story_spec должен быть хотя бы один источник
        # креатива (link_data / video_data / template_data). Иначе Meta вернёт ошибку.
        if not cls._has_creative_source(object_story_spec, image_hash, video_id):
            raise ValueError(
                "creative: object_story_spec должен содержать link_data/video_data/template_data "
                "ИЛИ передай top-level image_hash/video_id"
            )

        # Если top-level image_hash/video_id заданы — пробрасываем в creative,
        # чтобы handler был backward-compatible с упрощённым форматом.
        if image_hash:
            if not isinstance(image_hash, str):
                raise ValueError(f"creative.image_hash: ожидается str, получено {image_hash!r}")
            body["image_hash"] = image_hash
        if video_id:
            if not isinstance(video_id, str):
                raise ValueError(f"creative.video_id: ожидается str, получено {video_id!r}")
            body["video_id"] = video_id

        return body

    @staticmethod
    def _has_creative_source(
        object_story_spec: dict[str, Any],
        image_hash: Any,
        video_id: Any,
    ) -> bool:
        """Проверить, что есть хотя бы один источник креатива.

        Допускаем: link_data, video_data, template_data, text_data — внутри
        object_story_spec, ИЛИ top-level image_hash/video_id.
        """
        for key in ("link_data", "video_data", "template_data", "text_data", "photo_data"):
            if isinstance(object_story_spec.get(key), dict):
                return True
        return bool(image_hash or video_id)

    @classmethod
    def _build_ad_body(
        cls,
        spec: dict[str, Any],
        *,
        adset_ref: str,
        creative_ref: str,
    ) -> dict[str, Any]:
        name = cls._validate_name(spec.get("name"), section="ad")
        status = cls._validate_status(spec.get("status", "PAUSED"), section="ad")

        # creative — это JSON {"creative_id": "{result=creative:$.id}"}.
        # Marketing API хочет его именно как JSON-объект.
        return {
            "name": name,
            "adset_id": adset_ref,
            "creative": {"creative_id": creative_ref},
            "status": status,
        }

    # ====================== Field validators ======================

    @staticmethod
    def _validate_name(value: Any, *, section: str) -> str:
        if not isinstance(value, str):
            raise ValueError(f"{section}.name: ожидается строка, получено {value!r}")
        name = value.strip()
        if len(name) < 3:
            raise ValueError(f"{section}.name: минимум 3 символа")
        if len(name) > 400:
            raise ValueError(f"{section}.name: слишком длинное ({len(name)} > 400)")
        return name

    @staticmethod
    def _validate_objective(value: Any) -> str:
        if not isinstance(value, str):
            raise ValueError(f"campaign.objective: ожидается строка, получено {value!r}")
        normalized = value.strip().upper()
        if normalized not in _VALID_OBJECTIVES:
            raise ValueError(
                f"campaign.objective: допустимо {sorted(_VALID_OBJECTIVES)}, получено {value!r}"
            )
        return normalized

    @staticmethod
    def _validate_status(value: Any, *, section: str) -> str:
        if not isinstance(value, str):
            raise ValueError(f"{section}.status: ожидается строка, получено {value!r}")
        normalized = value.strip().upper()
        if normalized not in ("PAUSED", "ACTIVE"):
            raise ValueError(f"{section}.status: допустимо PAUSED или ACTIVE, получено {value!r}")
        return normalized

    @staticmethod
    def _validate_special_categories(value: Any) -> list[str]:
        if not isinstance(value, list) or not value:
            raise ValueError(
                "campaign.special_ad_categories: ожидается непустой список (минимум ['NONE'])"
            )
        normalized: list[str] = []
        for item in value:
            if not isinstance(item, str):
                raise ValueError(
                    f"campaign.special_ad_categories: элементы должны быть строками, получен {item!r}"
                )
            up = item.strip().upper()
            if up not in _VALID_SPECIAL_CATEGORIES:
                raise ValueError(
                    f"campaign.special_ad_categories: недопустимое значение {item!r}, "
                    f"ожидается из {sorted(_VALID_SPECIAL_CATEGORIES)}"
                )
            normalized.append(up)
        return normalized

    @staticmethod
    def _validate_cents(value: Any, *, field_name: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{field_name}: ожидается int центов, получено {value!r}")
        if value <= 0:
            raise ValueError(f"{field_name}: должен быть > 0, получено {value}")
        return value

    @staticmethod
    def _validate_billing_event(value: Any) -> str:
        if not isinstance(value, str):
            raise ValueError(f"adset.billing_event: ожидается строка, получено {value!r}")
        normalized = value.strip().upper()
        if normalized not in _VALID_BILLING_EVENTS:
            raise ValueError(
                f"adset.billing_event: допустимо {sorted(_VALID_BILLING_EVENTS)}, получено {value!r}"
            )
        return normalized

    @staticmethod
    def _validate_optimization_goal(value: Any) -> str:
        # Meta допускает много значений: LEAD_GENERATION, LINK_CLICKS, CONVERSIONS, etc.
        # Не пытаемся всё перечислить — просто требуем непустую строку в UPPER.
        if not isinstance(value, str):
            raise ValueError(f"adset.optimization_goal: ожидается строка, получено {value!r}")
        normalized = value.strip().upper()
        if not normalized:
            raise ValueError("adset.optimization_goal: пустая строка")
        return normalized

    @staticmethod
    def _validate_targeting(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict) or not value:
            raise ValueError("adset.targeting: обязателен и должен быть непустым dict")
        # Минимальная проверка: должно быть хотя бы geo_locations.
        if not isinstance(value.get("geo_locations"), dict):
            raise ValueError(
                "adset.targeting.geo_locations: обязательно (dict), минимум для запуска"
            )
        return value

    @staticmethod
    def _extract_ids(sub_results: list[dict[str, Any]]) -> dict[str, str | None]:
        """Из normalized batch response достать ID для каждого шага.

        Порядок шагов: campaign(0), adset(1), creative(2), ad(3).
        """
        order = ("campaign_id", "adset_id", "creative_id", "ad_id")
        result: dict[str, str | None] = dict.fromkeys(order, None)
        for r in sub_results:
            idx = r.get("index")
            if not isinstance(idx, int) or idx >= len(order):
                continue
            if not r.get("success"):
                continue
            body = r.get("body")
            if isinstance(body, dict):
                val = body.get("id")
                if val:
                    result[order[idx]] = str(val)
        return result
