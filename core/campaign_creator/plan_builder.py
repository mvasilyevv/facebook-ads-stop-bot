# -*- coding: utf-8 -*-
"""PlanBuilder: разворачивает CampaignSpec в линейный список PlanAction."""

from __future__ import annotations

from core.campaign_creator.plan_types import AdsetSpec, CampaignSpec, PlanAction


def build_plan(spec: CampaignSpec) -> list[PlanAction]:
    plan: list[PlanAction] = []
    plan.append(
        PlanAction(
            "create_campaign",
            {
                "offer_code": spec.offer_code,
                "iter_num": spec.iter_num,
                "campaign_name": spec.campaign_name,
                "budget_level": spec.budget_level,
            },
        )
    )

    # CBO: бюджет задаётся на уровне кампании ДО перехода в адсет.
    if spec.budget_level == "CBO":
        plan.append(
            PlanAction(
                "set_budget",
                {"daily_budget": spec.daily_budget, "level": "CBO"},
            )
        )

    # Переход с уровня кампании на уровень первого адсета.
    plan.append(PlanAction("click_next", {}, idempotent=False))

    first = spec.adsets[0]
    # Первый адсет тоже переименовываем: «1» или «1 | <suffix>».
    plan.append(PlanAction("rename_adset", {"adset_idx": 0, "suffix": first.name_suffix}))
    plan.extend(_adset_setup(first, idx=0, spec=spec))
    # Переход с уровня адсета на уровень объявления.
    plan.append(PlanAction("click_next_to_ad", {}, idempotent=False))
    plan.extend(_ads_for_first_adset(first, adset_idx=0))

    for i, adset in enumerate(spec.adsets[1:], start=1):
        plan.append(PlanAction("duplicate_adset", {"source_idx": 0}, idempotent=False))
        plan.append(PlanAction("switch_to_adset", {"adset_idx": i}))
        plan.append(PlanAction("rename_adset", {"adset_idx": i, "suffix": adset.name_suffix}))
        plan.extend(_adset_setup(adset, idx=i, spec=spec))
        plan.extend(_ads_for_duplicated_adset(adset, adset_idx=i))

    plan.append(PlanAction("save_draft", {}))
    return plan


def _adset_setup(adset: AdsetSpec, *, idx: int, spec: CampaignSpec) -> list[PlanAction]:
    actions: list[PlanAction] = [
        PlanAction("set_conversion_location", {"adset_idx": idx}),
        PlanAction("set_pixel_event", {"adset_idx": idx, "pixel_id": spec.pixel_id}),
        PlanAction("set_attribution", {"adset_idx": idx, "days": spec.attribution_days}),
    ]
    # ABO: бюджет на уровне адсета. CBO уже выставлен на кампании.
    if spec.budget_level == "ABO":
        actions.append(
            PlanAction(
                "set_budget",
                {
                    "adset_idx": idx,
                    "daily_budget": spec.daily_budget,
                    "level": "ABO",
                },
            )
        )
    actions.extend(
        [
            PlanAction("set_schedule_start", {"adset_idx": idx}),
            PlanAction("set_geo", {"adset_idx": idx, "countries": list(spec.countries)}),
            PlanAction("set_age", {"adset_idx": idx}),
        ]
    )
    return actions


def _ads_for_first_adset(adset: AdsetSpec, *, adset_idx: int) -> list[PlanAction]:
    out: list[PlanAction] = []
    first_creo = adset.creatives[0]
    out.append(PlanAction("rename_ad", {"adset_idx": adset_idx, "ad_idx": 0, "suffix": first_creo}))
    out.append(
        PlanAction(
            "upload_creatives",
            {
                "adset_idx": adset_idx,
                "ad_idx": 0,
                "file": first_creo,
                "subfolder": adset.creo_subfolder,
            },
            idempotent=False,
        )
    )
    out.append(
        PlanAction(
            "fill_texts",
            {
                "adset_idx": adset_idx,
                "ad_idx": 0,
                "headline": adset.headline,
                "primary_text": adset.primary_text,
            },
        )
    )
    out.append(PlanAction("set_cta", {"adset_idx": adset_idx, "ad_idx": 0}))
    for j, creo in enumerate(adset.creatives[1:], start=1):
        out.append(
            PlanAction(
                "duplicate_ad",
                {"adset_idx": adset_idx, "source_ad_idx": 0},
                idempotent=False,
            )
        )
        out.append(PlanAction("rename_ad", {"adset_idx": adset_idx, "ad_idx": j, "suffix": creo}))
        out.append(
            PlanAction(
                "reattach_creative",
                {
                    "adset_idx": adset_idx,
                    "ad_idx": j,
                    "file": creo,
                    "subfolder": adset.creo_subfolder,
                },
                idempotent=False,
            )
        )
    return out


def _ads_for_duplicated_adset(adset: AdsetSpec, *, adset_idx: int) -> list[PlanAction]:
    """После duplicate_adset уже есть объявления-копии. Перепривязываем креативы."""
    out: list[PlanAction] = []
    for j, creo in enumerate(adset.creatives):
        if j > 0:
            out.append(
                PlanAction(
                    "duplicate_ad",
                    {"adset_idx": adset_idx, "source_ad_idx": 0},
                    idempotent=False,
                )
            )
        out.append(PlanAction("rename_ad", {"adset_idx": adset_idx, "ad_idx": j, "suffix": creo}))
        out.append(
            PlanAction(
                "reattach_creative",
                {
                    "adset_idx": adset_idx,
                    "ad_idx": j,
                    "file": creo,
                    "subfolder": adset.creo_subfolder,
                },
                idempotent=False,
            )
        )
    out.append(
        PlanAction(
            "fill_texts",
            {
                "adset_idx": adset_idx,
                "ad_idx": 0,
                "headline": adset.headline,
                "primary_text": adset.primary_text,
            },
        )
    )
    out.append(PlanAction("set_cta", {"adset_idx": adset_idx, "ad_idx": 0}))
    return out
