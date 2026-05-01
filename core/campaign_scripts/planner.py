# -*- coding: utf-8 -*-
"""Расчёт плана создания кампании без действий в интерфейсе Ads Manager."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from core.campaign_scripts.creative_folder import CampaignCreativeFolder

DEFAULT_CONVERSION_EVENT = "Покупка"
ANTARCTICA_LOCATION = "Антарктика"
DEFAULT_SUB2 = "MV"
CAMPAIGN_NAME_PREFIX = "MV"
CAMPAIGN_NAME_SUFFIX = "adset.pro"


class CampaignScriptPlanError(ValueError):
    """Ошибка входных данных для плана создания кампании."""


@dataclass(frozen=True)
class CampaignScriptConfig:
    """Параметры создания кампании из UI."""

    offer_code: str
    offer_country_name: str
    cabinet_id: str
    sub2: str = DEFAULT_SUB2
    generation_date: date | None = None


@dataclass(frozen=True)
class CampaignAdPlan:
    """План одного объявления."""

    name: str
    media_file_name: str
    media_search_name: str
    media_path: str
    media_type: str
    url_params: str


@dataclass(frozen=True)
class CampaignAdSetPlan:
    """План одной группы объявлений."""

    name: str
    folder_path: str
    ads: list[CampaignAdPlan]


@dataclass(frozen=True)
class CampaignLocationPlan:
    """Правила выбора гео в Ads Manager."""

    add_locations: list[str]
    offer_country_name: str
    required_location_type: str
    remove_initial_location_after_add: bool
    rejected_location_terms: list[str]


@dataclass(frozen=True)
class CampaignManualGuideItem:
    """Одно значение ручного помощника для копирования в Ads Manager."""

    label: str
    value: str
    copyable: bool = True


@dataclass(frozen=True)
class CampaignManualGuideSection:
    """Секция ручного помощника создания кампании."""

    title: str
    items: list[CampaignManualGuideItem]


@dataclass(frozen=True)
class CampaignScriptPlan:
    """Полный безопасный план для ручного создания кампании."""

    campaign_name: str
    offer_code: str
    offer_country_name: str
    creative_folder_name: str
    creative_folder_path: str
    conversion_event: str
    cabinet_id: str
    sub2: str
    media_type: str
    adset_count: int
    ad_count: int
    adsets: list[CampaignAdSetPlan]
    location_plan: CampaignLocationPlan
    manual_guide: list[CampaignManualGuideSection]
    safety_notes: list[str]


def _normalize_required_text(value: str, field_name: str) -> str:
    """Очищает обязательное текстовое поле."""
    normalized = " ".join(str(value or "").strip().split())
    if not normalized:
        raise CampaignScriptPlanError(f"Заполните поле «{field_name}»")
    return normalized


def _build_url_params(*, sub2: str, ad_name: str, cabinet_id: str) -> str:
    """Собирает строку URL-параметров с макросами Meta."""
    return (
        f"sub2={sub2}"
        f"&sub3={ad_name}"
        f"&sub4={cabinet_id}"
        f"&sub5={{{{campaign.name}}}}"
        f"&sub6={{{{adset.name}}}}"
        f"&sub7={{{{ad.name}}}}"
    )


def _split_offer_code(value: str) -> tuple[str, str]:
    """Достаёт гео и слот из кода оффера."""
    parts = [part.strip() for part in value.split("_", 1)]
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise CampaignScriptPlanError("Код оффера должен быть в формате GEO_SLOT, например DRC_CR2")
    return parts[0], parts[1]


def _build_campaign_name(*, offer_code: str, generation_date: date | None) -> str:
    """Собирает название кампании по рабочему шаблону."""
    geo_name, slot_name = _split_offer_code(offer_code)
    launch_date = (generation_date or date.today()) + timedelta(days=1)
    return (
        f"{CAMPAIGN_NAME_PREFIX} | {geo_name} | {slot_name} | "
        f"{CAMPAIGN_NAME_SUFFIX} | {launch_date:%d.%m}"
    )


def _guide_item(label: str, value: str, *, copyable: bool = True) -> CampaignManualGuideItem:
    """Создаёт строку ручного помощника."""
    normalized = str(value or "").strip()
    return CampaignManualGuideItem(
        label=label,
        value=normalized or "—",
        copyable=copyable and bool(normalized),
    )


def _build_manual_guide(
    *,
    campaign_name: str,
    adsets: list[CampaignAdSetPlan],
    location_plan: CampaignLocationPlan,
) -> list[CampaignManualGuideSection]:
    """Собирает ручной чек-лист с кликабельными значениями."""
    first_adset = adsets[0] if adsets else None
    first_ads = first_adset.ads if first_adset else []
    sections = [
        CampaignManualGuideSection(
            title="Кампания",
            items=[
                _guide_item("Название", campaign_name),
            ],
        ),
        CampaignManualGuideSection(
            title="Группа объявлений",
            items=[
                _guide_item("Гео 1", ANTARCTICA_LOCATION),
                _guide_item("Гео 2", location_plan.offer_country_name),
            ],
        ),
    ]

    ad_items: list[CampaignManualGuideItem] = []
    for index, ad in enumerate(first_ads, start=1):
        ad_items.extend(
            [
                _guide_item(f"Ad {index}: название", ad.name),
                _guide_item(f"Ad {index}: поиск медиа", ad.media_search_name),
                _guide_item(f"Ad {index}: URL params", ad.url_params),
            ]
        )
    sections.append(CampaignManualGuideSection(title="Объявления", items=ad_items))

    copied_items: list[CampaignManualGuideItem] = []
    for adset_index, adset in enumerate(adsets[1:], start=2):
        copied_items.append(_guide_item(f"Группа {adset_index}: имя", adset.name))
        for ad_index, ad in enumerate(adset.ads, start=1):
            copied_items.extend(
                [
                    _guide_item(f"Группа {adset_index}, ad {ad_index}: название", ad.name),
                    _guide_item(
                        f"Группа {adset_index}, ad {ad_index}: поиск медиа", ad.media_search_name
                    ),
                ]
            )
    sections.append(CampaignManualGuideSection(title="Копии групп", items=copied_items))

    return sections


def build_campaign_script_plan(
    *,
    folder: CampaignCreativeFolder,
    config: CampaignScriptConfig,
) -> CampaignScriptPlan:
    """Строит план создания кампании из проверенной папки и настроек UI."""
    offer_code = _normalize_required_text(config.offer_code, "Оффер").upper()
    campaign_name = _build_campaign_name(
        offer_code=offer_code,
        generation_date=config.generation_date,
    )
    offer_country_name = _normalize_required_text(config.offer_country_name, "Страна оффера")
    cabinet_id = _normalize_required_text(config.cabinet_id, "ID кабинета")
    sub2 = _normalize_required_text(config.sub2, "sub2")

    adsets: list[CampaignAdSetPlan] = []
    for creative_adset in folder.adsets:
        ads = [
            CampaignAdPlan(
                name=creative_file.ad_name,
                media_file_name=creative_file.media_file_name,
                media_search_name=creative_file.media_search_name,
                media_path=creative_file.media_path,
                media_type=creative_file.media_type,
                url_params=_build_url_params(
                    sub2=sub2,
                    ad_name=creative_file.ad_name,
                    cabinet_id=cabinet_id,
                ),
            )
            for creative_file in creative_adset.files
        ]
        adsets.append(
            CampaignAdSetPlan(
                name=creative_adset.name,
                folder_path=creative_adset.folder_path,
                ads=ads,
            )
        )

    ad_count = sum(len(adset.ads) for adset in adsets)
    location_plan = CampaignLocationPlan(
        add_locations=[ANTARCTICA_LOCATION, offer_country_name],
        offer_country_name=offer_country_name,
        required_location_type="Страна/регион",
        remove_initial_location_after_add=True,
        rejected_location_terms=["город", "city", "область", "region", "Kasai-Occidental"],
    )
    return CampaignScriptPlan(
        campaign_name=campaign_name,
        offer_code=offer_code,
        offer_country_name=offer_country_name,
        creative_folder_name=folder.name,
        creative_folder_path=folder.path,
        conversion_event=DEFAULT_CONVERSION_EVENT,
        cabinet_id=cabinet_id,
        sub2=sub2,
        media_type=folder.media_type,
        adset_count=len(adsets),
        ad_count=ad_count,
        adsets=adsets,
        location_plan=location_plan,
        manual_guide=_build_manual_guide(
            campaign_name=campaign_name,
            adsets=adsets,
            location_plan=location_plan,
        ),
        safety_notes=[
            "Не нажимать Опубликовать без отдельного явного разрешения",
            "Если точный медиафайл уже есть в медиатеке, использовать его без повторной загрузки",
            "План предназначен только для ручного создания кампании",
            "Гео оффера выбирать только как Страна/регион, города и регионы отклонять",
        ],
    )
