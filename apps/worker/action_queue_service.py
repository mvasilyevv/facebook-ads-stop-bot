from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from apps.worker.pipeline_support import (
    build_action_notification_payload,
    map_action_type_to_event_type,
    map_action_type_to_source,
)
from core.actions import BrowserActionResult
from core.domain import ActionExecutionStatus, ActionType
from core.repositories import (
    ActionJobsRepository,
    AdsRepository,
    BrowserRepository,
    DecisionsRepository,
    WatchlistRepository,
)
from core.repositories.notification_outbox import NotificationOutboxRepository


@dataclass(slots=True, frozen=True)
class ReadyActionJob:
    """Снимок готовой action job для выполнения вне ORM-сессии."""

    id: str
    decision_id: str | None
    fb_ad_id: str
    profile_id: str | None
    browser_host_id: str | None
    action_type: ActionType
    attempt_count: int


class ActionQueueService:
    """Исполняет очередь pause/resume jobs по профилям."""

    def __init__(
        self,
        *,
        async_session_factory,
        pause_executor: Any | None,
        resume_executor: Any | None = None,
        profile_concurrency: int = 1,
        max_attempts: int = 3,
        retry_delay_seconds: int = 30,
    ) -> None:
        self._async_session_factory = async_session_factory
        self._pause_executor = pause_executor
        self._resume_executor = resume_executor or pause_executor
        self._profile_concurrency = max(int(profile_concurrency), 1)
        self._max_attempts = max(int(max_attempts), 1)
        self._retry_delay_seconds = max(int(retry_delay_seconds), 5)

    async def run_once(self, *, limit: int = 50) -> int:
        async with self._async_session_factory() as session:
            ready_jobs = await ActionJobsRepository(session).list_ready_jobs(limit=limit)
            grouped: dict[str, list[ReadyActionJob]] = defaultdict(list)
            for job in ready_jobs:
                if job.profile_id is None or job.browser_host_id is None:
                    continue
                grouped[str(job.profile_id)].append(
                    ReadyActionJob(
                        id=str(job.id),
                        decision_id=str(job.decision_id) if job.decision_id is not None else None,
                        fb_ad_id=job.fb_ad_id,
                        profile_id=str(job.profile_id) if job.profile_id is not None else None,
                        browser_host_id=(
                            str(job.browser_host_id) if job.browser_host_id is not None else None
                        ),
                        action_type=job.action_type,
                        attempt_count=job.attempt_count,
                    )
                )

        semaphore = asyncio.Semaphore(self._profile_concurrency)
        tasks = [
            asyncio.create_task(
                self._process_profile_jobs_guarded(
                    semaphore=semaphore,
                    profile_id=profile_id,
                    jobs=jobs,
                )
            )
            for profile_id, jobs in grouped.items()
        ]
        if tasks:
            await asyncio.gather(*tasks)
        return sum(len(jobs) for jobs in grouped.values())

    async def _process_profile_jobs_guarded(
        self,
        *,
        semaphore: asyncio.Semaphore,
        profile_id: str,
        jobs: list[ReadyActionJob],
    ) -> None:
        async with semaphore:
            await self._process_profile_jobs(profile_id=profile_id, jobs=jobs)

    async def _process_profile_jobs(
        self,
        *,
        profile_id: str,
        jobs: list[ReadyActionJob],
    ) -> None:
        async with self._async_session_factory() as session:
            browser_repo = BrowserRepository(session)
            profile = await browser_repo.get_profile(profile_id)
            if profile is None:
                return
            browser_host_id = jobs[0].browser_host_id
            if browser_host_id is None:
                return
            browser_host = await browser_repo.get_browser_host(browser_host_id)
            if browser_host is None:
                return
            vendor_profile_id = profile.vendor_profile_id
            browser_host_name = browser_host.name

        jobs_by_action: dict[ActionType, list[ReadyActionJob]] = defaultdict(list)
        for job in jobs:
            jobs_by_action[job.action_type].append(job)

        for action_type, action_jobs in jobs_by_action.items():
            started_at = datetime.now(tz=UTC)
            for job in action_jobs:
                async with self._async_session_factory() as session:
                    repo = ActionJobsRepository(session)
                    await repo.mark_running(job.id, started_at=started_at)
                    await session.commit()

            results = await self._execute_batch(
                action_type=action_type,
                vendor_profile_id=vendor_profile_id,
                browser_host_name=browser_host_name,
                fb_ad_ids=[job.fb_ad_id for job in action_jobs],
            )
            results_by_ad_id = {result.fb_ad_id: result for result in results}
            for job in action_jobs:
                result = results_by_ad_id.get(job.fb_ad_id)
                if result is None:
                    result = BrowserActionResult(
                        success=False,
                        message=(f"Исполнитель не вернул результат для объявления {job.fb_ad_id}"),
                        fb_ad_id=job.fb_ad_id,
                        profile_id=vendor_profile_id,
                        browser_host_name=browser_host_name,
                    )
                await self._finalize_job(
                    job=job,
                    result=result,
                    started_at=started_at,
                )

    async def _execute_batch(
        self,
        *,
        action_type: ActionType,
        vendor_profile_id: str,
        browser_host_name: str,
        fb_ad_ids: list[str],
    ) -> list[BrowserActionResult]:
        executor = (
            self._pause_executor if action_type == ActionType.PAUSE else self._resume_executor
        )
        if executor is None:
            return [
                BrowserActionResult(
                    success=False,
                    message=f"Не настроен исполнитель действия {action_type.value}",
                    fb_ad_id=fb_ad_id,
                    profile_id=vendor_profile_id,
                    browser_host_name=browser_host_name,
                )
                for fb_ad_id in fb_ad_ids
            ]

        batch_method_name = "pause_ads" if action_type == ActionType.PAUSE else "resume_ads"
        batch_method = getattr(executor, batch_method_name, None)
        if callable(batch_method):
            return list(await batch_method(vendor_profile_id, browser_host_name, fb_ad_ids))

        single_method_name = "pause_ad" if action_type == ActionType.PAUSE else "resume_ad"
        single_method = getattr(executor, single_method_name, None)
        if not callable(single_method):
            return [
                BrowserActionResult(
                    success=False,
                    message=f"Не найден метод исполнителя для действия {action_type.value}",
                    fb_ad_id=fb_ad_id,
                    profile_id=vendor_profile_id,
                    browser_host_name=browser_host_name,
                )
                for fb_ad_id in fb_ad_ids
            ]
        results: list[BrowserActionResult] = []
        for fb_ad_id in fb_ad_ids:
            results.append(await single_method(vendor_profile_id, browser_host_name, fb_ad_id))
        return results

    async def _finalize_job(
        self,
        *,
        job: ReadyActionJob,
        result: BrowserActionResult,
        started_at: datetime,
    ) -> None:
        finished_at = datetime.now(tz=UTC)
        action_status = (
            ActionExecutionStatus.SUCCEEDED if result.success else ActionExecutionStatus.FAILED
        )
        async with self._async_session_factory() as session:
            action_jobs_repo = ActionJobsRepository(session)
            decisions_repo = DecisionsRepository(session)
            ads_repo = AdsRepository(session)
            watchlist_repo = WatchlistRepository(session)
            outbox_repo = NotificationOutboxRepository(session)

            if result.success:
                await action_jobs_repo.mark_succeeded(job.id, finished_at=finished_at)
            else:
                next_attempt_number = job.attempt_count + 1
                if next_attempt_number >= self._max_attempts:
                    await action_jobs_repo.mark_failed(
                        job.id,
                        finished_at=finished_at,
                        error=result.message,
                    )
                else:
                    await action_jobs_repo.mark_retrying(
                        job.id,
                        next_attempt_at=finished_at
                        + timedelta(seconds=self._retry_delay_seconds * next_attempt_number),
                        error=result.message,
                    )

            if job.decision_id is not None:
                await decisions_repo.add_action_execution(
                    decision_id=job.decision_id,
                    action_type=job.action_type,
                    status=action_status,
                    started_at=started_at,
                    finished_at=finished_at,
                    message=result.message,
                )
                await decisions_repo.set_decision_action_result(
                    decision_id=job.decision_id,
                    action_executed=result.success,
                    action_status=action_status.value,
                )

            ad = await ads_repo.get_ad_by_fb_id(job.fb_ad_id)
            if result.success and ad is not None:
                action_source = map_action_type_to_source(job.action_type)
                if action_source is not None:
                    await ads_repo.update_ad_review_state(
                        job.fb_ad_id,
                        last_action_source=action_source,
                        last_action_at=finished_at,
                    )
                if job.action_type == ActionType.PAUSE:
                    await watchlist_repo.delete_entry(job.fb_ad_id)

                event_type = map_action_type_to_event_type(job.action_type)
                if event_type is not None:
                    await outbox_repo.enqueue(
                        decision_id=job.decision_id,
                        event_type=event_type,
                        payload_json=build_action_notification_payload(
                            ad=ad,
                            fb_ad_id=job.fb_ad_id,
                            message=result.message,
                        ),
                    )

            await session.commit()
            if not result.success:
                logging.getLogger(__name__).warning(
                    "Не удалось выполнить %s для объявления %s: %s",
                    job.action_type.value,
                    job.fb_ad_id,
                    result.message,
                )
