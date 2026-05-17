# -*- coding: utf-8 -*-
"""Dry-run автосоздателя кампаний поверх PlanRunner.

Запускает 1×1 (или N×M через аргументы) на оффере KE_CR2 без участия API/БД,
чтобы проверить рефакторинг шагов и эвристические селекторы duplicate/reattach.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from clients.python_grpc.client import BrowserAgentClient, BrowserAgentConfig
from core.campaign_creator.naming import build_campaign_name
from core.campaign_creator.plan_builder import build_plan
from core.campaign_creator.plan_types import AdsetSpec, CampaignSpec
from core.campaign_creator.step_executor import execute_plan, open_page
from core.campaign_creator.steps.base import StepContext
from core.config import get_settings
from core.db import get_session_factory
from core.domain import CampaignCreatorTaskStatus
from core.models import Offer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("dry_run")


async def _load_offer(code: str) -> Offer:
    async with get_session_factory()() as s:
        offer = (await s.execute(select(Offer).where(Offer.code == code))).scalar_one_or_none()
        if offer is None:
            raise SystemExit(f"Оффер не найден: {code}")
        return offer


def _build_specs(
    offer: Offer,
    *,
    n_adsets: int,
    m_ads: int,
    creo_root: Path,
    iter_num: int,
    daily_budget: float,
    attribution_days: int,
    start_date: str,
    adset_subtype: str,
) -> tuple[CampaignSpec, StepContext]:
    # Креативы в creo_root/{subfolder}/*.jpeg — берём первые m_ads из подпапки "1".
    files_in_1 = sorted(p.name for p in (creo_root / "1").glob("*.jpeg"))
    if len(files_in_1) < m_ads:
        raise SystemExit(f"В {creo_root}/1 найдено {len(files_in_1)} креативов, нужно {m_ads}")
    chosen = files_in_1[:m_ads]

    campaign_name = build_campaign_name(
        iter_num=iter_num,
        geo_code=offer.geo_code or offer.code,
        date=start_date,
    )

    adsets_plan = [
        AdsetSpec(
            name_suffix=adset_subtype,
            creo_subfolder=str(idx + 1) if (creo_root / str(idx + 1)).exists() else "1",
            headline="Заголовок dry-run",
            primary_text="Основной текст dry-run",
            creatives=list(chosen),
        )
        for idx in range(n_adsets)
    ]

    spec = CampaignSpec(
        offer_code=offer.code,
        cabinet_id=offer.cabinet_id,
        pixel_id=offer.pixel_id,
        landing_url=offer.landing_url,
        countries=[offer.geo_slot_name],
        daily_budget=daily_budget,
        attribution_days=attribution_days,
        budget_level="CBO",
        adsets=adsets_plan,
        campaign_name=campaign_name,
        iter_num=iter_num,
    )

    context = StepContext(
        offer_code=offer.code,
        cabinet_id=offer.cabinet_id,
        campaign_name=campaign_name,
        pixel_id=offer.pixel_id,
        landing_url=offer.landing_url,
        geo_code=offer.geo_code or "",
        geo_slot_name=offer.geo_slot_name,
        daily_budget=daily_budget,
        attribution_days=attribution_days,
        budget_level="CBO",
        iter_num=iter_num,
        adsets=[
            AdsetSpec(name_suffix=a.name_suffix, headline=a.headline, primary_text=a.primary_text)
            for a in adsets_plan
        ],
        creo_folder=str(creo_root),
        extra={"offer_country_name": offer.country_name or ""},
    )
    return spec, context


def _make_browser_client() -> BrowserAgentClient:
    s = get_settings()
    return BrowserAgentClient(
        BrowserAgentConfig(
            vision_x_token=s.vision_x_token,
            vision_api_url=s.vision_api_url,
            vision_profile_id=s.vision_profile_id,
        )
    )


async def _set_status(
    status: CampaignCreatorTaskStatus, *, step: str | None = None, data: dict | None = None
) -> None:
    msg = f"[STATUS] {status.value}"
    if step:
        msg += f" step={step}"
    if data:
        msg += f" data={data}"
    logger.info(msg)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offer", default="KE_CR2")
    ap.add_argument("--adsets", type=int, default=1)
    ap.add_argument("--ads", type=int, default=1)
    ap.add_argument(
        "--creo",
        default="/Users/markvasilev/Documents/FB_Agent_Creo/KE_CR2_2026-05-10_15-58-07_3creo_3copies",
    )
    ap.add_argument("--iter", type=int, default=99, dest="iter_num")
    ap.add_argument("--budget", type=float, default=10.0)
    ap.add_argument(
        "--attribution",
        type=int,
        choices=[1, 7],
        default=7,
        help="Окно атрибуции в днях (1 или 7)",
    )
    ap.add_argument(
        "--start-date",
        default=None,
        help="Дата старта DD.MM (по умолчанию завтра)",
    )
    ap.add_argument(
        "--adset-subtype",
        default="",
        help="Подтип адсета — попадает в имя как '1 | <subtype>' (пусто = просто '1')",
    )
    ap.add_argument("--print-only", action="store_true", help="Только распечатать план")
    ap.add_argument(
        "--stop-after",
        default=None,
        help="Имя шага, после которого остановиться (план обрезается по step.name)",
    )
    ap.add_argument(
        "--start-from",
        default=None,
        help="Имя шага, с которого начать (предыдущие шаги пропускаются)",
    )
    args = ap.parse_args()

    offer = await _load_offer(args.offer)
    # Дата старта: если не передана — завтра.
    start_date = args.start_date or (datetime.now() + timedelta(days=1)).strftime("%d.%m")
    spec, context = _build_specs(
        offer,
        n_adsets=args.adsets,
        m_ads=args.ads,
        creo_root=Path(args.creo),
        iter_num=args.iter_num,
        daily_budget=args.budget,
        attribution_days=args.attribution,
        start_date=start_date,
        adset_subtype=args.adset_subtype,
    )

    plan = build_plan(spec)
    if args.start_from:
        names = [a.step for a in plan]
        if args.start_from not in names:
            raise SystemExit(f"--start-from: шаг {args.start_from!r} не найден в плане")
        plan = plan[names.index(args.start_from) :]
    if args.stop_after:
        names = [a.step for a in plan]
        if args.stop_after not in names:
            raise SystemExit(f"--stop-after: шаг {args.stop_after!r} не найден в плане")
        plan = plan[: names.index(args.stop_after) + 1]
    logger.info("Кампания: %s", spec.campaign_name)
    logger.info("План (%d шагов):", len(plan))
    for i, a in enumerate(plan):
        logger.info("  %02d. %-25s %s", i, a.step, a.params)

    if args.print_only:
        return 0

    client = _make_browser_client()
    async with open_page(client) as page:
        ok = await execute_plan(plan, page, context, _set_status)
    logger.info("Готово: success=%s", ok)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
