# -*- coding: utf-8 -*-
"""Раннер автопилота создания FB-кампании GH/Aviator (GH_AVI).

Строит 1 кампанию × 5 адсетов × 3 объявления (uniquify-копии) PAUSED-черновиком
в Ads Manager через Python-автопилот (build_plan → шаги Vision), как заливали KE_CR2.

Режимы:
    python scripts/run_creator_gh_avi.py --print   # показать spec + все шаги, БЕЗ браузера
    python scripts/run_creator_gh_avi.py --run      # боевой залив в живой Vision-браузер

Боевой режим: пауза observer (is_scanning_enabled=false) на время сборки → автопилот →
восстановление флага в finally. Кампания создаётся PAUSED — байер ревьюит и сам unpause.

ПРЕДУСЛОВИЕ: Vision-браузер открыт на нужном кабинете (act_26943307705301002),
стек поднят (browser-agent :50051), старого черновика GH_AVI в кабинете нет.
"""

from __future__ import annotations

import asyncio
import os
import sys

# repo root в sys.path — чтобы скрипт работал и как `python scripts/...`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from core.campaign_creator.plan_builder import build_plan
from core.campaign_creator.plan_runner import PlanRunner
from core.campaign_creator.plan_types import FBState
from core.campaign_creator.spec_builder import build_campaign_spec_from_folder
from core.campaign_creator.step_executor import open_page
from core.campaign_creator.steps.base import StepContext
from core.campaign_creator.steps.registry import STEP_REGISTRY
from core.config import get_settings

# ====================== Параметры кампании GH_AVI ======================

CREO_ROOT = os.path.expanduser("~/Documents/FB_Agent_Creo/GH_AVI_campaign01")
OFFER_CODE = "GH_AVI"
CABINET_ID = "act_26943307705301002"
CAMPAIGN_NAME = "MV | GH | AVI | adset.pro | 03.06"  # дата = следующий день (запуск с новых суток)
PIXEL_ID = "1282495953856981"
LANDING_URL = "https://space2go.forum/track/6a1f37ebf10ec2c6fce437c6/ads"
GEO_CODE = "GH"
GEO_NAME = "Гана"  # русская локаль FB Ads Manager — гео ищется по этому имени
DAILY_BUDGET = 2.99
ATTRIBUTION_DAYS = 7
BUDGET_LEVEL = "ABO"

# Угол per-адсет (sub6 = {{adset.name}}) — порядок = CR001..CR005 (подпапки 1..5).
ANGLES = [
    "Proof Post / MoMo",  # CR001 live-пост
    "Before/After / Free Bets",  # CR002 before/after
    "FOMO / Friends",  # CR003 чат
    "Adrenaline / Cashout",  # CR004 геймплей
    "Football / Black Stars",  # CR005 футбол
]

# Единый winner-угол payment-trust для всех 5 адсетов (переменная теста = ВИЗУАЛ).
PRIMARY_TEXT = (
    "Deposit just GHS 10, play Aviator, and cash out straight to your MTN MoMo 💸✈️\n"
    "New players get 20 FREE BETS on your first deposit! 🇬🇭\n"
    "Small start, real wins — withdraw to MoMo anytime. Play now!"
)
HEADLINE = "Deposit GHS 10 → Get 20 Free Bets on Aviator"
DESCRIPTION = "Cash out wins straight to MTN MoMo. Fast & safe."


# ====================== Сборка spec / plan ======================


def build_spec():
    """CampaignSpec из канон-папки + per-адсет углы."""
    spec = build_campaign_spec_from_folder(
        creo_folder=CREO_ROOT,
        offer_code=OFFER_CODE,
        cabinet_id=CABINET_ID,
        pixel_id=PIXEL_ID,
        landing_url=LANDING_URL,
        countries=[GEO_NAME],
        daily_budget=DAILY_BUDGET,
        attribution_days=ATTRIBUTION_DAYS,
        budget_level=BUDGET_LEVEL,
        primary_text=PRIMARY_TEXT,
        headline=HEADLINE,
        description=DESCRIPTION,
        campaign_name=CAMPAIGN_NAME,
    )
    # Патчим имя-угол на каждый адсет: имя адсета станет "N | <angle>" → sub6.
    for i, adset in enumerate(spec.adsets):
        if i < len(ANGLES):
            adset.name_suffix = ANGLES[i]
    return spec


def build_context(spec) -> StepContext:
    return StepContext(
        offer_code=spec.offer_code,
        cabinet_id=spec.cabinet_id,
        campaign_name=spec.campaign_name or CAMPAIGN_NAME,
        pixel_id=spec.pixel_id,
        landing_url=spec.landing_url,
        geo_code=GEO_CODE,
        geo_slot_name=GEO_NAME,
        daily_budget=spec.daily_budget,
        attribution_days=spec.attribution_days,
        budget_level=spec.budget_level,
        iter_num=spec.iter_num,
        adsets=spec.adsets,
        creo_folder=CREO_ROOT,
    )


# ====================== Пауза observer ======================


async def set_observer_scanning(engine, enabled: bool) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE observer_config SET is_scanning_enabled = :v"),
            {"v": enabled},
        )


# ====================== Режимы ======================


def print_plan() -> None:
    spec = build_spec()
    plan = build_plan(spec)
    print("=" * 70)
    print(f"КАМПАНИЯ: {spec.campaign_name}")
    print(f"  offer={spec.offer_code} cabinet={spec.cabinet_id} pixel={spec.pixel_id}")
    print(
        f"  гео={GEO_NAME}({GEO_CODE}) бюджет={spec.budget_level} ${spec.daily_budget}/адсет/день"
    )
    print(f"  attribution={spec.attribution_days}d landing={spec.landing_url}")
    print(f"  адсетов={len(spec.adsets)}")
    for i, a in enumerate(spec.adsets):
        print(
            f"    AS{i + 1}: имя='{a.display_name(i)}' subfolder={a.subfolder(i)} "
            f"креативов={len(a.creatives)} {a.creatives}"
        )
    print(f"\nПЛАН: {len(plan)} шагов")
    for i, action in enumerate(plan):
        print(f"  {i:>3}. {action.step:<22} {action.params}")
    print("=" * 70)
    itog_ads = sum(len(a.creatives) for a in spec.adsets)
    print(
        f"ИТОГ: 1 кампания × {len(spec.adsets)} адсетов × {itog_ads} объявлений (PAUSED-черновик)"
    )


async def run_live() -> int:
    from clients.python_grpc.client import BrowserAgentClient, BrowserAgentConfig

    settings = get_settings()
    spec = build_spec()
    plan = build_plan(spec)
    ctx = build_context(spec)

    engine = create_async_engine(settings.database_url, echo=False)
    client = BrowserAgentClient(
        BrowserAgentConfig(
            grpc_host=os.environ.get("BROWSER_AGENT_HOST", "localhost"),
            grpc_port=int(os.environ.get("BROWSER_AGENT_GRPC_PORT", "50051")),
            vision_x_token=settings.vision_x_token,
            vision_api_url=settings.vision_api_url,
            vision_profile_id=settings.vision_profile_id,
        )
    )

    def on_status(idx: int, name: str, status: str, message: str | None = None) -> None:
        mark = {"RUNNING": "▶", "SUCCEEDED": "✓", "FAILED": "✗"}.get(status, "·")
        line = f"  {mark} [{idx:>3}] {name:<22} {status}"
        if message:
            line += f" — {message}"
        print(line, flush=True)

    print("⏸  Ставлю observer на паузу (is_scanning_enabled=false)…", flush=True)
    await set_observer_scanning(engine, False)
    await asyncio.sleep(3)  # дать текущему циклу скана завершиться

    ok = False
    try:
        async with open_page(client) as page:
            print(f"🌐 Активная вкладка: {page.url}", flush=True)
            print(f"🚀 Запускаю автопилот: {len(plan)} шагов → PAUSED-черновик\n", flush=True)
            runner = PlanRunner(STEP_REGISTRY)
            state = {"progress_index": 0, "fb_state": FBState()}
            ok = await runner.run(page, ctx, plan, state, on_status)
    finally:
        print("\n▶️  Возвращаю observer (is_scanning_enabled=true)…", flush=True)
        try:
            await set_observer_scanning(engine, True)
        except Exception as exc:  # noqa: BLE001
            print(f"⚠️  Не удалось вернуть observer: {exc} — включи вручную!", flush=True)
        await engine.dispose()

    if ok:
        print(f"\n✅ Готово: черновик '{spec.campaign_name}' создан (PAUSED). Ревью → unpause.")
        return 0
    print(
        "\n❌ Автопилот упал (см. шаг FAILED выше). Черновик может быть частичным — проверь кабинет."
    )
    return 1


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "--print"
    if mode == "--print":
        print_plan()
        return 0
    if mode == "--run":
        return asyncio.run(run_live())
    print(f"Неизвестный режим {mode!r}. Используй --print или --run")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
