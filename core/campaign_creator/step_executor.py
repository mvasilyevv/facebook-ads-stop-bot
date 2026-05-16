# -*- coding: utf-8 -*-
"""Универсальный исполнитель шагов campaign_creator.

Используется и full-pipeline, и run-step / run-from / resume.
Инкапсулирует подключение к CDP-браузеру через Vision и общий цикл по шагам.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Awaitable, Callable

from playwright.async_api import Browser, Page, async_playwright

from clients.python_grpc.client import BrowserAgentClient
from core.campaign_creator.plan_runner import PlanRunner
from core.campaign_creator.plan_types import FBState, PlanAction
from core.campaign_creator.steps.base import BaseStep, StepContext, StepResult
from core.campaign_creator.steps.registry import STEP_REGISTRY
from core.domain import CampaignCreatorTaskStatus

logger = logging.getLogger(__name__)

SetStatus = Callable[..., Awaitable[None]]


def normalize_cdp_url(url: str) -> str:
    """Playwright connect_over_cdp ждёт HTTP-URL, не WS."""
    if url.startswith("ws://"):
        return "http://" + url[len("ws://") :]
    if url.startswith("wss://"):
        return "https://" + url[len("wss://") :]
    return url


@asynccontextmanager
async def open_page(client: BrowserAgentClient):
    """Подключиться к CDP браузеру Vision и выдать активную Page.

    Гарантирует закрытие Playwright-браузера и gRPC-канала клиента в finally.
    """
    await client.start()
    await client.start_browser()
    cdp_url = client.cdp_url
    if not cdp_url:
        await client.close()
        raise RuntimeError("Vision не вернул cdp_port после старта браузера")
    cdp_url = normalize_cdp_url(cdp_url)

    pw = await async_playwright().start()
    browser: Browser | None = None
    try:
        browser = await pw.chromium.connect_over_cdp(cdp_url)
        pages = browser.contexts[0].pages if browser.contexts else []
        page = pages[0] if pages else await browser.new_page()
        yield page
    finally:
        if browser is not None:
            try:
                await browser.close()
            except Exception:
                logger.exception("Не удалось закрыть Playwright browser")
        await pw.stop()
        try:
            await client.disconnect_browser()
        except Exception:
            logger.exception("Не удалось отключить browser-agent")
        await client.close()


async def execute_steps(
    steps: list[BaseStep],
    page: Page,
    context: StepContext,
    set_status: SetStatus,
) -> bool:
    """Выполнить список шагов последовательно. True — все ок, False — упал.

    На входе/выходе ставит статусы RUNNING/SUCCEEDED/FAILED и обновляет current_step.
    """
    total = len(steps)
    for idx, step in enumerate(steps, start=1):
        logger.info("Выполняю шаг %d/%d: %s", idx, total, step.name)
        await set_status(CampaignCreatorTaskStatus.RUNNING, step=step.name)
        result: StepResult = await step.execute(page, context)
        if not result.success:
            await set_status(
                CampaignCreatorTaskStatus.FAILED,
                step=step.name,
                data={"error": result.message},
            )
            logger.error("Шаг %s провалился: %s", step.name, result.message)
            return False
        logger.info("Шаг %s завершён: %s", step.name, result.message)

    await set_status(CampaignCreatorTaskStatus.SUCCEEDED)
    return True


async def execute_plan(
    plan: list[PlanAction],
    page: Page,
    context: StepContext,
    set_status: SetStatus,
    *,
    state: dict | None = None,
) -> bool:
    """Выполнить декларативный план через PlanRunner.

    state — изменяемый dict: {"progress_index": int, "fb_state": FBState}.
    Если None — создаётся свежий. Вызывающая сторона может передать state,
    чтобы переиспользовать FBState между запусками (resume).
    """
    if state is None:
        state = {"progress_index": 0, "fb_state": FBState()}

    pending: list = []

    def _on_status(idx: int, name: str, status: str, message: str | None = None) -> None:
        pending.append((idx, name, status, message))

    runner = PlanRunner(STEP_REGISTRY)
    ok = await runner.run(page, context, plan, state, _on_status)

    if ok:
        await set_status(CampaignCreatorTaskStatus.SUCCEEDED)
    else:
        last_failed = next((t for t in reversed(pending) if t[2] == "FAILED"), None)
        if last_failed:
            await set_status(
                CampaignCreatorTaskStatus.FAILED,
                step=last_failed[1],
                data={"error": last_failed[3] or "FAILED"},
            )
        else:
            await set_status(CampaignCreatorTaskStatus.FAILED)
    return ok
