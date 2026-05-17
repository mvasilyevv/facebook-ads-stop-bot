# -*- coding: utf-8 -*-
"""Высокоуровневый билдер CampaignSpec по папке креативов.

Объединяет creo_scanner + CampaignSpec в одну функцию: пользователь
передаёт корневую папку креативов и общие тексты — получает готовый spec,
который можно прокинуть в build_plan.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from core.campaign_creator.creo_scanner import scan_creo_folder
from core.campaign_creator.plan_types import CampaignSpec


def build_campaign_spec_from_folder(
    *,
    creo_folder: str | Path,
    offer_code: str,
    cabinet_id: str,
    pixel_id: str,
    landing_url: str,
    countries: list[str],
    daily_budget: float,
    attribution_days: Literal[1, 7],
    budget_level: Literal["CBO", "ABO"],
    primary_text: str = "",
    headline: str = "",
    description: str = "",
    name_suffix: str = "",
    campaign_name: str | None = None,
    iter_num: int = 1,
) -> CampaignSpec:
    """Собрать CampaignSpec из папки креативов и общих параметров.

    Сканирует creo_folder через creo_scanner.scan_creo_folder и применяет
    общие тексты ко всем адсетам. Бросает ValueError, если папка пустая
    или невалидная.
    """
    adsets = scan_creo_folder(
        creo_folder,
        name_suffix=name_suffix,
        headline=headline,
        primary_text=primary_text,
        description=description,
    )
    return CampaignSpec(
        offer_code=offer_code,
        cabinet_id=cabinet_id,
        pixel_id=pixel_id,
        landing_url=landing_url,
        countries=countries,
        daily_budget=daily_budget,
        attribution_days=attribution_days,
        budget_level=budget_level,
        adsets=adsets,
        campaign_name=campaign_name,
        iter_num=iter_num,
    )
