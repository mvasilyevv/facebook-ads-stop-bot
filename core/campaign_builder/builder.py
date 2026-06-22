# -*- coding: utf-8 -*-
"""Движок сборки FB-кампании: чистый build_campaign_spec + execute-скелет.

`build_campaign_spec(config)` — чистая функция без I/O: разворачивает CampaignConfig
в план объектов (campaign → adsets → ads) с отрендеренными именами, телами Graph API
и статусами по launch_state. Используется для dry-run/validate (UI-превью) и как
вход для воркера-исполнителя.

Тела объектов (campaign/adset/creative/ad) — порт из `scripts/fb_launch.py` без форка
логики. Канал исполнения (ExecuteGraphCall через Vision + MediaUploader) живёт в воркере;
здесь только спека и параметризованный порядок шагов.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.campaign_builder.config import (
    CampaignBlock,
    CampaignConfig,
    LaunchState,
)
from core.campaign_builder.naming import creative_codes, render_name

# ---------------------- спека (план объектов) ----------------------


@dataclass(frozen=True)
class AdSpec:
    """План одного объявления (1 концепт → 1 уникальная копия)."""

    code: str  # OFFER_CRxxx, идёт в имя ad/creative и sub3
    url_tags: str
    status: str  # ACTIVE | PAUSED по launch_state


@dataclass(frozen=True)
class AdsetSpec:
    """План одного adset."""

    name: str
    body: dict
    status: str
    ads: list[AdSpec]


@dataclass(frozen=True)
class CampaignSpec_Block:
    """План одной кампании."""

    key: str
    name: str
    kind: str  # image | video
    body: dict
    status: str
    adsets: list[AdsetSpec]


@dataclass(frozen=True)
class CampaignSpec:
    """Полный план запуска (все кампании конфига)."""

    offer_code: str
    launch_state: LaunchState
    copies_per_concept: int
    campaigns: list[CampaignSpec_Block] = field(default_factory=list)


# ---------------------- статусы по launch_state ----------------------


def _child_status(launch_state: LaunchState) -> str:
    """Статус adset'ов и ads: ACTIVE при campaign_paused, иначе PAUSED."""
    return "ACTIVE" if launch_state == LaunchState.CAMPAIGN_PAUSED else "PAUSED"


# ---------------------- тела объектов (Graph API) ----------------------


def campaign_body(cfg: CampaignConfig, name: str) -> dict:
    """Тело кампании. Кампания всегда PAUSED (money-инвариант)."""
    body = {
        "name": name,
        "objective": cfg.objective,
        "status": "PAUSED",
        "special_ad_categories": cfg.special_ad_categories,
    }
    if cfg.budget.level == "campaign":  # CBO: бюджет+стратегия на кампании
        body["daily_budget"] = cfg.budget.daily_cents
        body["bid_strategy"] = cfg.budget.bid_strategy
        if cfg.budget.bid_amount_cents:
            body["bid_amount"] = cfg.budget.bid_amount_cents
    return body


def adset_body(cfg: CampaignConfig, name: str, status: str) -> dict:
    """Тело adset. campaign_id подставляется на исполнении (batch JSONPath / id)."""
    body: dict = {
        "name": name,
        "billing_event": "IMPRESSIONS",
        "optimization_goal": cfg.optimization_goal,
        "destination_type": "WEBSITE",
        "promoted_object": {
            "pixel_id": cfg.account.pixel_id,
            "custom_event_type": cfg.custom_event_type,
            "smart_pse_enabled": False,
        },
        "attribution_spec": cfg.attribution.spec(),
        "targeting": {
            "geo_locations": {
                "countries": cfg.targeting.geo_countries(),
                "location_types": cfg.targeting.location_types,
            },
            "age_min": cfg.targeting.age_min,
            "age_max": cfg.targeting.age_max,
            "targeting_automation": {
                "advantage_audience": 1 if cfg.targeting.advantage_audience else 0
            },
        },
        "start_time": cfg.start_time,
        "status": status,
    }
    if cfg.budget.level == "adset":  # ABO: бюджет+стратегия на адсете
        body["daily_budget"] = cfg.budget.daily_cents
        body["bid_strategy"] = cfg.budget.bid_strategy
        if cfg.budget.bid_amount_cents:
            body["bid_amount"] = cfg.budget.bid_amount_cents
    return body


def _link_data(cfg: CampaignConfig, media: dict) -> dict:
    """link_data для image-креатива (link + CTA + image_hash + опц. текст)."""
    ld: dict = {
        "link": cfg.destination_link,
        "call_to_action": {"type": cfg.cta, "value": {"link": cfg.destination_link}},
    }
    ld.update(media)
    if cfg.ad_text.mode == "full":
        if cfg.ad_text.message:
            ld["message"] = cfg.ad_text.message
        if cfg.ad_text.headline:
            ld["name"] = cfg.ad_text.headline
        if cfg.ad_text.description:
            ld["description"] = cfg.ad_text.description
    return ld


def image_creative_body(cfg: CampaignConfig, name: str, image_hash: str, url_tags: str) -> dict:
    """Тело image-креатива."""
    return {
        "name": name,
        "object_story_spec": {
            "page_id": cfg.account.page_id,
            "link_data": _link_data(cfg, {"image_hash": image_hash}),
        },
        "url_tags": url_tags,
        "degrees_of_freedom_spec": {
            "creative_features_spec": {
                "text_optimizations": {"enroll_status": cfg.text_optimizations}
            }
        },
    }


def video_creative_body(
    cfg: CampaignConfig, name: str, video_id: str, thumb_hash: str, url_tags: str
) -> dict:
    """Тело video-креатива."""
    vd: dict = {
        "video_id": video_id,
        "image_hash": thumb_hash,
        "call_to_action": {"type": cfg.cta, "value": {"link": cfg.destination_link}},
    }
    if cfg.ad_text.mode == "full":
        if cfg.ad_text.message:
            vd["message"] = cfg.ad_text.message
        if cfg.ad_text.headline:
            vd["title"] = cfg.ad_text.headline
        if cfg.ad_text.description:
            vd["link_description"] = cfg.ad_text.description
    return {
        "name": name,
        "object_story_spec": {"page_id": cfg.account.page_id, "video_data": vd},
        "url_tags": url_tags,
        "degrees_of_freedom_spec": {
            "creative_features_spec": {
                "text_optimizations": {"enroll_status": cfg.text_optimizations}
            }
        },
    }


def ad_body(name: str, adset_id: str, creative_id: str, status: str) -> dict:
    """Тело ad (adset_id и creative_id подставляются на исполнении)."""
    return {
        "name": name,
        "adset_id": adset_id,
        "creative": {"creative_id": creative_id},
        "status": status,
    }


def url_tags_of(cfg: CampaignConfig, code: str) -> str:
    """url_tags по SOP (sub2..sub7), sub3 = код креатива."""
    return (
        f"sub2={cfg.byer_tag}"
        f"&sub3={code}"
        f"&sub4={cfg.account.act_num}"
        "&sub5={{campaign.name}}"
        "&sub6={{adset.name}}"
        "&sub7={{ad.name}}"
    )


# ---------------------- сборка спеки ----------------------


def _build_block(cfg: CampaignConfig, block: CampaignBlock, copies: int) -> CampaignSpec_Block:
    """Разворачивает одну CampaignBlock в план с отрендеренными именами и телами."""
    child_status = _child_status(cfg.launch_state)
    camp_name = render_name(
        block.name, byer=cfg.byer_tag, offer=cfg.offer_code, date_label=cfg.date_label
    )

    adsets: list[AdsetSpec] = []
    for adset_cfg in block.adsets:
        adset_name = render_name(
            adset_cfg.name, byer=cfg.byer_tag, offer=cfg.offer_code, date_label=cfg.date_label
        )
        # copies ad-слотов на adset; код креатива OFFER_CRxxx с глобальной нумерацией адсета.
        codes = creative_codes(cfg.offer_code, count=copies, prefix=cfg.creative_prefix)
        ads = [
            AdSpec(code=code, url_tags=url_tags_of(cfg, code), status=child_status)
            for code in codes
        ]
        adsets.append(
            AdsetSpec(
                name=adset_name,
                body=adset_body(cfg, adset_name, child_status),
                status=child_status,
                ads=ads,
            )
        )

    return CampaignSpec_Block(
        key=block.key,
        name=camp_name,
        kind=block.kind,
        body=campaign_body(cfg, camp_name),
        status="PAUSED",
        adsets=adsets,
    )


def build_campaign_spec(cfg: CampaignConfig) -> CampaignSpec:
    """Чистая функция: CampaignConfig → план объектов (для dry-run/validate/воркера).

    copies_per_concept по умолчанию = числу adset'ов в кампании (раскладка K×N из дизайна).
    Имена отрендерены, тела готовы к Graph API, статусы выставлены по launch_state.
    """
    blocks: list[CampaignSpec_Block] = []
    reported_copies = 0  # репрезентативный скаляр для UI = значение первого блока
    for index, block in enumerate(cfg.campaigns):
        # Дефолт copies на блок = числу adset'ов ЭТОГО блока (раскладка K×N).
        # Явный copies_per_concept переопределяет для всех блоков.
        copies = cfg.copies_per_concept if cfg.copies_per_concept is not None else len(block.adsets)
        if index == 0:
            reported_copies = copies
        blocks.append(_build_block(cfg, block, copies))

    return CampaignSpec(
        offer_code=cfg.offer_code,
        launch_state=cfg.launch_state,
        copies_per_concept=reported_copies,
        campaigns=blocks,
    )


# ---------------------- execute-скелет (порядок шагов) ----------------------
#
# Реальный I/O (ExecuteGraphCall через Vision + MediaUploader) живёт в воркере
# (Волна 2). Здесь — чистый, тестируемый план шагов: фиксирует порядок
# campaign → adsets → upload media → creatives → ads и статусы по launch_state.
# Воркер итерирует по этим шагам, подставляя реальные Meta-ID между батчами.


# Допустимые типы шагов исполнения (детерминированный порядок).
EXEC_STEP_ORDER = ("campaign", "adsets", "upload", "creatives", "ads")


@dataclass(frozen=True)
class ExecStep:
    """Один шаг исполнения для одной кампании."""

    kind: str  # один из EXEC_STEP_ORDER
    campaign_key: str
    status: str  # статус создаваемых объектов на этом шаге (PAUSED/ACTIVE/"" для upload)


def plan_execution_steps(spec: CampaignSpec) -> list[ExecStep]:
    """Чистый план шагов исполнения по спеке (без I/O).

    Для каждой кампании порядок строго: campaign → adsets → upload → creatives →
    ads. campaign всегда PAUSED, adsets/ads — по launch_state (ACTIVE при
    campaign_paused). upload media статуса не имеет.
    """
    steps: list[ExecStep] = []
    for block in spec.campaigns:
        child_status = block.adsets[0].status if block.adsets else _child_status(spec.launch_state)
        steps.append(ExecStep(kind="campaign", campaign_key=block.key, status="PAUSED"))
        steps.append(ExecStep(kind="adsets", campaign_key=block.key, status=child_status))
        steps.append(ExecStep(kind="upload", campaign_key=block.key, status=""))
        steps.append(ExecStep(kind="creatives", campaign_key=block.key, status=""))
        steps.append(ExecStep(kind="ads", campaign_key=block.key, status=child_status))
    return steps
