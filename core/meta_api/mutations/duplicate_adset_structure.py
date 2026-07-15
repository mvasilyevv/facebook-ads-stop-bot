# -*- coding: utf-8 -*-
"""Duplicate a selected ad-set structure without deep-copying every source ad.

The request is deliberately executed in small, observable Graph calls. Meta's
``/{adset_id}/copies`` edge is used with ``deep_copy=false`` so the source
ad-set settings are preserved but no source ads are copied implicitly. Only the
explicitly selected source ads are then recreated with their existing
``creative_id``.

This mutation is irreversible. Once the first target object exists, a lost
response cannot be retried safely. Every failure after writes begin therefore
raises :class:`DuplicateAdsetStructurePartialError` and includes every known
created id. Cleanup is best-effort PAUSE, never DELETE.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

from core.meta_api.client import MetaApiClient
from core.meta_api.mutations.base import MutationValidationError, require_numeric_id, success_result
from core.meta_api.mutations.set_adset_budget import MAX_DAILY_BUDGET_CENTS
from core.meta_api.schemas import MetaMutationPayload

logger = logging.getLogger(__name__)

_CAMPAIGN_FIELDS = (
    "id,account_id,name,objective,special_ad_categories,special_ad_category_country,"
    "buying_type,bid_strategy,status,daily_budget"
)
_ADSET_FIELDS = (
    "id,account_id,campaign_id,name,status,effective_status,daily_budget,start_time,"
    "billing_event,optimization_goal,bid_strategy,bid_amount,targeting,promoted_object,"
    "attribution_spec,destination_type,pacing_type"
)
_SOURCE_AD_FIELDS = "id,account_id,campaign_id,adset_id,name,status,creative{id}"
_VERIFY_AD_FIELDS = "id,adset_id,status,effective_status,creative{id}"
_RECOVERY_STALE_SECONDS = max(
    1,
    int(os.environ.get("RECONCILER_STUCK_TIMEOUT_MIN", "30")) * 60,
)
_RECONCILER_POLL_SECONDS = max(1, int(os.environ.get("RECONCILER_INTERVAL_SEC", "30")))
_RECOVERY_SAFETY_MARGIN_SECONDS = 60
_START_TIME_GUARD = timedelta(
    seconds=(_RECOVERY_STALE_SECONDS + _RECONCILER_POLL_SECONDS + _RECOVERY_SAFETY_MARGIN_SECONDS)
)
_START_TIME_GUARD_MINUTES = math.ceil(_START_TIME_GUARD.total_seconds() / 60)
_MAX_SELECTED_ADS = 10
_MAX_CREATED_ADS = 50

DuplicateProgressCallback = Callable[[dict[str, Any]], Awaitable[None]]


class DuplicateAdsetStructurePartialError(Exception):
    """A structure may exist in Meta and must never be retried blindly."""

    def __init__(
        self,
        message: str,
        *,
        created_ids: dict[str, list[str]],
        failed_steps: list[dict[str, Any]],
        cleanup_failures: list[dict[str, str]] | None = None,
    ) -> None:
        super().__init__(message)
        self.created_ids = {key: list(value) for key, value in created_ids.items()}
        self.failed_steps = list(failed_steps)
        self.cleanup_failures = list(cleanup_failures or [])


@dataclass(frozen=True)
class _Plan:
    account_id: str
    source_campaign_id: str
    source_adset_id: str
    selected_ad_ids: tuple[str, ...]
    campaign_count: int
    adsets_per_campaign: int
    budget_level: str
    daily_budget_cents: int
    start_time: str
    start_at: datetime
    campaign_names: tuple[str, ...]
    adset_names: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class _SourceAd:
    ad_id: str
    name: str
    creative_id: str


class DuplicateAdsetStructureHandler:
    """Create N paused campaigns, M shallow ad-set copies, and selected ads."""

    mutation_kind: ClassVar[str] = "duplicate_adset_structure"

    async def execute(
        self,
        client: MetaApiClient,
        payload: MetaMutationPayload,
        *,
        progress_callback: DuplicateProgressCallback | None = None,
    ) -> dict[str, Any]:
        plan = self._validate_plan(payload)
        source_campaign, source_adset, source_ads = await self._load_and_validate_source(
            client, payload, plan
        )

        created: dict[str, list[str]] = {"campaigns": [], "adsets": [], "ads": []}
        activated: dict[str, list[str]] = {"campaigns": [], "adsets": [], "ads": []}
        graph_responses: list[dict[str, Any]] = []
        target_adsets: list[tuple[str, str]] = []
        target_ads: dict[str, list[tuple[str, _SourceAd]]] = {}
        current_step = "create_campaign"

        try:
            for campaign_index, campaign_name in enumerate(plan.campaign_names):
                current_step = f"create_campaign[{campaign_index}]"
                campaign_response = await client.execute_graph_call(
                    ad_account_id=payload.ad_account_id,
                    method="POST",
                    endpoint=f"/{plan.account_id}/campaigns",
                    query_params={},
                    body_json=self._campaign_create_body(source_campaign, plan, campaign_name),
                )
                graph_responses.append(campaign_response)
                campaign_id = self._extract_created_id(campaign_response, "campaign")
                created["campaigns"].append(campaign_id)
                await self._emit_progress(
                    progress_callback,
                    phase="creating",
                    step=f"campaign_created[{campaign_index}]",
                    created=created,
                    activated=activated,
                )

                for adset_index in range(plan.adsets_per_campaign):
                    current_step = f"copy_adset[{campaign_index},{adset_index}]"
                    copy_response = await client.execute_graph_call(
                        ad_account_id=payload.ad_account_id,
                        method="POST",
                        endpoint=f"/{plan.source_adset_id}/copies",
                        query_params={},
                        # Marketing API AdSet Copies edge. deep_copy=false is the
                        # critical invariant: ads must be created only from selection.
                        body_json={
                            "campaign_id": campaign_id,
                            "deep_copy": False,
                            "status_option": "PAUSED",
                        },
                    )
                    graph_responses.append(copy_response)
                    adset_id = self._extract_created_id(copy_response, "adset")
                    created["adsets"].append(adset_id)
                    target_adsets.append((adset_id, campaign_id))
                    target_ads[adset_id] = []
                    await self._emit_progress(
                        progress_callback,
                        phase="creating",
                        step=f"adset_created[{campaign_index},{adset_index}]",
                        created=created,
                        activated=activated,
                    )

                    current_step = f"configure_adset[{campaign_index},{adset_index}]"
                    configure_response = await client.execute_graph_call(
                        ad_account_id=payload.ad_account_id,
                        method="POST",
                        endpoint=f"/{adset_id}",
                        query_params={},
                        body_json=self._adset_update_body(
                            plan,
                            plan.adset_names[campaign_index][adset_index],
                        ),
                    )
                    self._require_write_success(configure_response, current_step)
                    graph_responses.append(configure_response)

                    for source_ad in source_ads:
                        current_step = (
                            f"create_ad[{campaign_index},{adset_index},{source_ad.ad_id}]"
                        )
                        ad_response = await client.execute_graph_call(
                            ad_account_id=payload.ad_account_id,
                            method="POST",
                            endpoint=f"/{plan.account_id}/ads",
                            query_params={},
                            # Reuse the source creative object. No creative cloning.
                            body_json={
                                "name": source_ad.name,
                                "adset_id": adset_id,
                                "creative": {"creative_id": source_ad.creative_id},
                                "status": "PAUSED",
                            },
                        )
                        graph_responses.append(ad_response)
                        ad_id = self._extract_created_id(ad_response, "ad")
                        created["ads"].append(ad_id)
                        target_ads[adset_id].append((ad_id, source_ad))
                        await self._emit_progress(
                            progress_callback,
                            phase="creating",
                            step=(f"ad_created[{campaign_index},{adset_index},{source_ad.ad_id}]"),
                            created=created,
                            activated=activated,
                        )

            current_step = "verify_paused_structure"
            await self._emit_progress(
                progress_callback,
                phase="verifying_paused",
                step=current_step,
                created=created,
                activated=activated,
            )
            await self._verify_structure(
                client,
                payload,
                plan,
                created,
                target_adsets,
                target_ads,
            )
            # Validation also ran before the first Graph write, but a large 3-2-1
            # plan can take time to create. Re-check at the actual activation
            # boundary so a crash always has enough time to be detected and PAUSED
            # before Meta reaches the scheduled start_time.
            current_step = "activation_headroom"
            self._require_activation_headroom(plan)
            await self._emit_progress(
                progress_callback,
                phase="verified_paused",
                step="verify_paused_structure_complete",
                created=created,
                activated=activated,
            )

            # Activation is intentionally staged. Campaigns and selected ads are
            # activated first; ad sets are the final spend gate.
            # Persist this boundary before the first ACTIVE request. If the worker
            # dies after Meta commits but before returning the response, the stale
            # recovery path still knows every object that must be paused.
            await self._emit_progress(
                progress_callback,
                phase="activating",
                step="activation_started",
                created=created,
                activated=activated,
            )
            for campaign_id in created["campaigns"]:
                current_step = f"activate_campaign[{campaign_id}]"
                response = await self._set_status(client, payload, campaign_id, "ACTIVE")
                self._require_write_success(response, current_step)
                activated["campaigns"].append(campaign_id)
                await self._emit_progress(
                    progress_callback,
                    phase="activating",
                    step=current_step,
                    created=created,
                    activated=activated,
                )
            for ad_id in created["ads"]:
                current_step = f"activate_ad[{ad_id}]"
                response = await self._set_status(client, payload, ad_id, "ACTIVE")
                self._require_write_success(response, current_step)
                activated["ads"].append(ad_id)
                await self._emit_progress(
                    progress_callback,
                    phase="activating",
                    step=current_step,
                    created=created,
                    activated=activated,
                )
            for adset_id in created["adsets"]:
                current_step = f"activate_adset[{adset_id}]"
                response = await self._set_status(client, payload, adset_id, "ACTIVE")
                self._require_write_success(response, current_step)
                activated["adsets"].append(adset_id)
                await self._emit_progress(
                    progress_callback,
                    phase="activating",
                    step=current_step,
                    created=created,
                    activated=activated,
                )
            await self._emit_progress(
                progress_callback,
                phase="activated",
                step="activation_complete",
                created=created,
                activated=activated,
            )
        except asyncio.CancelledError:
            # asyncio cancellation is a BaseException and bypasses the normal
            # exception branch. Shield cleanup so graceful task cancellation gets
            # a best-effort PAUSE pass; a hard SIGKILL is handled from the persisted
            # checkpoint by the reconciler recovery flow.
            cleanup_failures = await self._pause_created_shielded(client, payload, created)
            try:
                await self._emit_progress(
                    progress_callback,
                    phase="cancelled_cleanup",
                    step=current_step,
                    created=created,
                    activated=activated,
                    cleanup_failures=cleanup_failures,
                )
            except Exception:  # noqa: BLE001 — cancellation must still propagate
                logger.exception("failed to persist duplicate cancellation cleanup checkpoint")
            raise
        except Exception as exc:
            cleanup_failures = await self._pause_created(client, payload, created)
            try:
                await self._emit_progress(
                    progress_callback,
                    phase="failed_cleanup",
                    step=current_step,
                    created=created,
                    activated=activated,
                    cleanup_failures=cleanup_failures,
                )
            except Exception:  # noqa: BLE001 — preserve the original mutation failure
                logger.exception("failed to persist duplicate failure cleanup checkpoint")
            logger.exception(
                "duplicate_adset_structure failed at %s; created=%s cleanup_failures=%s",
                current_step,
                created,
                cleanup_failures,
            )
            raise DuplicateAdsetStructurePartialError(
                f"duplicate_adset_structure: failure at {current_step}: {exc}",
                created_ids=created,
                failed_steps=[{"step": current_step, "error": repr(exc)}],
                cleanup_failures=cleanup_failures,
            ) from exc

        modified_ids = created["campaigns"] + created["adsets"] + created["ads"]
        return success_result(
            graph_response={"calls": graph_responses},
            modified_ids=modified_ids,
            extra={
                "source_campaign_id": plan.source_campaign_id,
                "source_adset_id": plan.source_adset_id,
                "created_ids": created,
                "campaign_count": len(created["campaigns"]),
                "adset_count": len(created["adsets"]),
                "ad_count": len(created["ads"]),
                "budget_level": plan.budget_level,
            },
        )

    @staticmethod
    async def _emit_progress(
        callback: DuplicateProgressCallback | None,
        *,
        phase: str,
        step: str,
        created: dict[str, list[str]],
        activated: dict[str, list[str]],
        cleanup_failures: list[dict[str, str]] | None = None,
    ) -> None:
        if callback is None:
            return
        checkpoint: dict[str, Any] = {
            "checkpoint_type": "duplicate_adset_structure",
            "checkpoint_version": 1,
            "phase": phase,
            "step": step,
            "created_ids": {key: list(created[key]) for key in ("campaigns", "adsets", "ads")},
            "activated_ids": {key: list(activated[key]) for key in ("campaigns", "adsets", "ads")},
            "checkpointed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }
        if cleanup_failures is not None:
            checkpoint["cleanup_failures"] = list(cleanup_failures)
        await callback(checkpoint)

    async def _pause_created_shielded(
        self,
        client: MetaApiClient,
        payload: MetaMutationPayload,
        created: dict[str, list[str]],
    ) -> list[dict[str, str]]:
        cleanup_task = asyncio.create_task(self._pause_created(client, payload, created))
        try:
            return await asyncio.shield(cleanup_task)
        except asyncio.CancelledError:
            # A second cancellation must not cancel the inner PAUSE pass. The task
            # remains referenced by the loop and may finish during graceful shutdown.
            logger.warning("duplicate cleanup received a second cancellation; PAUSE continues")
            raise

    @classmethod
    def created_ids_from_checkpoint(cls, checkpoint: dict[str, Any]) -> dict[str, list[str]]:
        """Validate persisted recovery input before issuing any Graph writes."""
        if checkpoint.get("checkpoint_type") != cls.mutation_kind:
            raise MutationValidationError("invalid duplicate recovery checkpoint type")
        raw = checkpoint.get("created_ids")
        if not isinstance(raw, dict):
            raise MutationValidationError("duplicate recovery checkpoint has no created_ids")
        limits = {"campaigns": 5, "adsets": 50, "ads": _MAX_CREATED_ADS}
        created: dict[str, list[str]] = {}
        for key, limit in limits.items():
            values = raw.get(key)
            if not isinstance(values, list) or len(values) > limit:
                raise MutationValidationError(f"invalid duplicate recovery {key}")
            created[key] = [require_numeric_id(value, f"recovery {key}") for value in values]
            if len(set(created[key])) != len(created[key]):
                raise MutationValidationError(f"duplicate ids in recovery {key}")
        if not any(created.values()):
            raise MutationValidationError("duplicate recovery checkpoint is empty")
        return created

    async def recover_checkpoint(
        self,
        client: MetaApiClient,
        payload: MetaMutationPayload,
        checkpoint: dict[str, Any],
    ) -> tuple[dict[str, list[str]], list[dict[str, str]]]:
        """PAUSE every checkpointed object; safe to call repeatedly."""
        created = self.created_ids_from_checkpoint(checkpoint)
        failures = await self._pause_created(client, payload, created)
        return created, failures

    @classmethod
    def _validate_plan(cls, payload: MetaMutationPayload) -> _Plan:
        params = payload.params or {}
        account_id = cls._validate_account_id(payload.ad_account_id)
        source_campaign_id = require_numeric_id(
            params.get("source_campaign_id"), "source_campaign_id"
        )
        source_adset_id = require_numeric_id(params.get("source_adset_id"), "source_adset_id")
        selected_ad_ids = cls._validate_ids(params.get("selected_ad_ids"), "selected_ad_ids")
        if len(selected_ad_ids) > _MAX_SELECTED_ADS:
            raise MutationValidationError(
                f"selected_ad_ids: at most {_MAX_SELECTED_ADS} ads are allowed"
            )
        campaign_count = cls._validate_count(params.get("campaign_count"), "campaign_count", 5)
        adsets_per_campaign = cls._validate_count(
            params.get("adsets_per_campaign"), "adsets_per_campaign", 10
        )
        created_ads = campaign_count * adsets_per_campaign * len(selected_ad_ids)
        if created_ads > _MAX_CREATED_ADS:
            raise MutationValidationError(
                "campaign_count * adsets_per_campaign * selected ads must be <= "
                f"{_MAX_CREATED_ADS} (got {created_ads})"
            )

        budget_level_raw = params.get("budget_level")
        if not isinstance(budget_level_raw, str):
            raise MutationValidationError("budget_level: expected ABO or CBO")
        budget_level = budget_level_raw.strip().upper()
        if budget_level not in {"ABO", "CBO"}:
            raise MutationValidationError("budget_level: expected ABO or CBO")

        daily_budget_cents = params.get("daily_budget_cents")
        if isinstance(daily_budget_cents, bool) or not isinstance(daily_budget_cents, int):
            raise MutationValidationError("daily_budget_cents: expected a positive integer")
        if not 1 <= daily_budget_cents <= MAX_DAILY_BUDGET_CENTS:
            raise MutationValidationError(
                f"daily_budget_cents: expected 1..{MAX_DAILY_BUDGET_CENTS}"
            )

        start_time, start_at = cls._validate_start_time(params.get("start_time"))
        campaign_names = cls._validate_names(
            params.get("campaign_names"), "campaign_names", campaign_count
        )
        adset_names = cls._validate_adset_names(
            params.get("adset_names"), campaign_count, adsets_per_campaign
        )
        return _Plan(
            account_id=account_id,
            source_campaign_id=source_campaign_id,
            source_adset_id=source_adset_id,
            selected_ad_ids=selected_ad_ids,
            campaign_count=campaign_count,
            adsets_per_campaign=adsets_per_campaign,
            budget_level=budget_level,
            daily_budget_cents=daily_budget_cents,
            start_time=start_time,
            start_at=start_at,
            campaign_names=campaign_names,
            adset_names=adset_names,
        )

    async def _load_and_validate_source(
        self,
        client: MetaApiClient,
        payload: MetaMutationPayload,
        plan: _Plan,
    ) -> tuple[dict[str, Any], dict[str, Any], tuple[_SourceAd, ...]]:
        campaign = await client.execute_graph_call(
            ad_account_id=payload.ad_account_id,
            method="GET",
            endpoint=f"/{plan.source_campaign_id}",
            query_params={"fields": _CAMPAIGN_FIELDS},
        )
        adset = await client.execute_graph_call(
            ad_account_id=payload.ad_account_id,
            method="GET",
            endpoint=f"/{plan.source_adset_id}",
            query_params={"fields": _ADSET_FIELDS},
        )
        self._require_object_id(campaign, plan.source_campaign_id, "source campaign")
        self._require_object_id(adset, plan.source_adset_id, "source adset")
        self._require_account(campaign, plan, "source campaign")
        self._require_account(adset, plan, "source adset")
        if str(adset.get("campaign_id") or "") != plan.source_campaign_id:
            raise MutationValidationError("source_adset_id does not belong to source_campaign_id")
        if not campaign.get("objective"):
            raise MutationValidationError("source campaign has no objective")

        ads: list[_SourceAd] = []
        for ad_id in plan.selected_ad_ids:
            source = await client.execute_graph_call(
                ad_account_id=payload.ad_account_id,
                method="GET",
                endpoint=f"/{ad_id}",
                query_params={"fields": _SOURCE_AD_FIELDS},
            )
            self._require_object_id(source, ad_id, "selected ad")
            self._require_account(source, plan, f"selected ad {ad_id}")
            if str(source.get("adset_id") or "") != plan.source_adset_id:
                raise MutationValidationError(
                    f"selected ad {ad_id} does not belong to source_adset_id"
                )
            if str(source.get("campaign_id") or "") != plan.source_campaign_id:
                raise MutationValidationError(
                    f"selected ad {ad_id} does not belong to source_campaign_id"
                )
            creative = source.get("creative")
            creative_id = creative.get("id") if isinstance(creative, dict) else None
            creative_id = require_numeric_id(creative_id, f"selected ad {ad_id} creative.id")
            name = str(source.get("name") or "").strip()
            if not name:
                raise MutationValidationError(f"selected ad {ad_id} has no name")
            ads.append(_SourceAd(ad_id=ad_id, name=name, creative_id=creative_id))
        return campaign, adset, tuple(ads)

    @staticmethod
    def _campaign_create_body(
        source: dict[str, Any], plan: _Plan, campaign_name: str
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "name": campaign_name,
            "objective": source["objective"],
            "special_ad_categories": source.get("special_ad_categories") or ["NONE"],
            "status": "PAUSED",
        }
        for key in (
            "buying_type",
            "bid_strategy",
            "special_ad_category_country",
        ):
            if source.get(key) not in (None, "", []):
                body[key] = source[key]
        if plan.budget_level == "CBO":
            body["daily_budget"] = plan.daily_budget_cents
        return body

    @staticmethod
    def _adset_update_body(plan: _Plan, adset_name: str) -> dict[str, Any]:
        body: dict[str, Any] = {
            "name": adset_name,
            "status": "PAUSED",
            "start_time": plan.start_time,
        }
        if plan.budget_level == "ABO":
            body["daily_budget"] = plan.daily_budget_cents
        return body

    async def _verify_structure(
        self,
        client: MetaApiClient,
        payload: MetaMutationPayload,
        plan: _Plan,
        created: dict[str, list[str]],
        target_adsets: list[tuple[str, str]],
        target_ads: dict[str, list[tuple[str, _SourceAd]]],
    ) -> None:
        expected_adsets = plan.campaign_count * plan.adsets_per_campaign
        expected_ads = expected_adsets * len(plan.selected_ad_ids)
        if len(created["campaigns"]) != plan.campaign_count:
            raise RuntimeError("campaign count mismatch")
        if len(created["adsets"]) != expected_adsets:
            raise RuntimeError("ad set count mismatch")
        if len(created["ads"]) != expected_ads:
            raise RuntimeError("ad count mismatch")

        for campaign_id in created["campaigns"]:
            row = await client.execute_graph_call(
                ad_account_id=payload.ad_account_id,
                method="GET",
                endpoint=f"/{campaign_id}",
                query_params={"fields": "id,status,daily_budget"},
            )
            self._require_object_id(row, campaign_id, "created campaign")
            self._require_paused(row, f"campaign {campaign_id}")
            if plan.budget_level == "CBO":
                self._require_budget(row, plan.daily_budget_cents, f"campaign {campaign_id}")

        for adset_id, campaign_id in target_adsets:
            row = await client.execute_graph_call(
                ad_account_id=payload.ad_account_id,
                method="GET",
                endpoint=f"/{adset_id}",
                query_params={
                    "fields": "id,campaign_id,status,daily_budget,lifetime_budget,start_time"
                },
            )
            self._require_object_id(row, adset_id, "created adset")
            self._require_paused(row, f"adset {adset_id}")
            if str(row.get("campaign_id") or "") != campaign_id:
                raise RuntimeError(f"adset {adset_id}: target campaign mismatch")
            if plan.budget_level == "ABO":
                self._require_budget(row, plan.daily_budget_cents, f"adset {adset_id}")
                if self._as_int(row.get("lifetime_budget"), default=0) != 0:
                    raise RuntimeError(f"adset {adset_id}: ABO target retained lifetime_budget")
            elif any(
                self._as_int(row.get(field), default=0) != 0
                for field in ("daily_budget", "lifetime_budget")
            ):
                raise RuntimeError(f"adset {adset_id}: CBO target retained an ad-set budget")
            if self._parse_graph_time(row.get("start_time")) != plan.start_at:
                raise RuntimeError(f"adset {adset_id}: start_time mismatch")

            ads_response = await client.execute_graph_call(
                ad_account_id=payload.ad_account_id,
                method="GET",
                endpoint=f"/{adset_id}/ads",
                query_params={"fields": _VERIFY_AD_FIELDS, "limit": "100"},
            )
            rows = ads_response.get("data") if isinstance(ads_response, dict) else None
            if not isinstance(rows, list):
                raise RuntimeError(f"adset {adset_id}: invalid ads verification response")
            expected = {ad_id: source.creative_id for ad_id, source in target_ads[adset_id]}
            actual: dict[str, str] = {}
            for ad_row in rows:
                if not isinstance(ad_row, dict):
                    raise RuntimeError(f"adset {adset_id}: invalid ad verification row")
                ad_id = str(ad_row.get("id") or "")
                creative = ad_row.get("creative")
                creative_id = str(creative.get("id") or "") if isinstance(creative, dict) else ""
                actual[ad_id] = creative_id
                self._require_paused(ad_row, f"ad {ad_id}")
            if actual != expected:
                raise RuntimeError(
                    f"adset {adset_id}: selected ad count/creative mismatch "
                    f"expected={expected!r} actual={actual!r}"
                )

    async def _pause_created(
        self,
        client: MetaApiClient,
        payload: MetaMutationPayload,
        created: dict[str, list[str]],
    ) -> list[dict[str, str]]:
        failures: list[dict[str, str]] = []
        # Campaign first is the fastest top-level spend gate. Then pause every
        # child independently in case a campaign status call failed.
        for object_type in ("campaigns", "adsets", "ads"):
            for object_id in created[object_type]:
                try:
                    response = await self._set_status(client, payload, object_id, "PAUSED")
                    self._require_write_success(response, f"cleanup_pause[{object_id}]")
                    row = await client.execute_graph_call(
                        ad_account_id=payload.ad_account_id,
                        method="GET",
                        endpoint=f"/{object_id}",
                        query_params={"fields": "id,status,effective_status"},
                    )
                    self._require_object_id(row, object_id, f"cleanup object {object_id}")
                    self._require_paused(row, f"cleanup object {object_id}")
                except Exception as exc:  # best effort: continue pausing the rest
                    failures.append({"id": object_id, "error": repr(exc)})
        return failures

    @staticmethod
    async def _set_status(
        client: MetaApiClient,
        payload: MetaMutationPayload,
        object_id: str,
        status: str,
    ) -> dict[str, Any]:
        return await client.execute_graph_call(
            ad_account_id=payload.ad_account_id,
            method="POST",
            endpoint=f"/{object_id}",
            query_params={},
            body_json={"status": status},
        )

    @staticmethod
    def _extract_created_id(response: dict[str, Any], object_type: str) -> str:
        keys = {
            "campaign": ("id", "campaign_id", "copied_campaign_id"),
            "adset": ("copied_adset_id", "adset_id", "id"),
            "ad": ("id", "ad_id"),
        }[object_type]
        candidates: list[dict[str, Any]] = [response]
        data = response.get("data") if isinstance(response, dict) else None
        if isinstance(data, list):
            candidates.extend(item for item in data if isinstance(item, dict))
        elif isinstance(data, dict):
            candidates.append(data)
        for item in candidates:
            for key in keys:
                value = item.get(key)
                if isinstance(value, str) and value.isdigit():
                    return value
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    return str(value)
        raise RuntimeError(f"{object_type} creation response has no numeric id: {response!r}")

    @staticmethod
    def _require_write_success(response: dict[str, Any], step: str) -> None:
        if not isinstance(response, dict) or response.get("success") is not True:
            raise RuntimeError(f"{step}: Graph write returned failure: {response!r}")

    @staticmethod
    def _require_object_id(row: dict[str, Any], expected: str, label: str) -> None:
        if not isinstance(row, dict) or str(row.get("id") or "") != expected:
            raise MutationValidationError(f"{label}: Graph returned the wrong object")

    @staticmethod
    def _require_account(row: dict[str, Any], plan: _Plan, label: str) -> None:
        account_id = str(row.get("account_id") or "").removeprefix("act_")
        expected = plan.account_id.removeprefix("act_")
        if account_id != expected:
            raise MutationValidationError(f"{label}: object belongs to another ad account")

    @staticmethod
    def _require_paused(row: dict[str, Any], label: str) -> None:
        # ``status`` is the object's configured delivery status.  Do not fall
        # back to ``effective_status``: a child may inherit PAUSED from a parent
        # while its own configured status is still ACTIVE, which would resume
        # spend as soon as that parent is enabled.
        status = str(row.get("status") or "").upper()
        if status != "PAUSED":
            raise RuntimeError(f"{label}: expected PAUSED, got {status or 'missing'}")

    @classmethod
    def _require_budget(cls, row: dict[str, Any], expected: int, label: str) -> None:
        if cls._as_int(row.get("daily_budget"), default=-1) != expected:
            raise RuntimeError(f"{label}: daily_budget mismatch")

    @staticmethod
    def _as_int(value: Any, *, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _validate_account_id(value: str | None) -> str:
        if not isinstance(value, str):
            raise MutationValidationError("ad_account_id is required")
        cleaned = value.strip()
        if not cleaned.startswith("act_") or not cleaned.removeprefix("act_").isdigit():
            raise MutationValidationError("ad_account_id must be act_<numeric id>")
        return cleaned

    @staticmethod
    def _validate_ids(value: Any, field_name: str) -> tuple[str, ...]:
        if not isinstance(value, list) or not value:
            raise MutationValidationError(f"{field_name}: expected a non-empty list")
        ids = tuple(require_numeric_id(item, field_name) for item in value)
        if len(set(ids)) != len(ids):
            raise MutationValidationError(f"{field_name}: duplicate ids are not allowed")
        return ids

    @staticmethod
    def _validate_count(value: Any, field_name: str, maximum: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
            raise MutationValidationError(f"{field_name}: expected integer 1..{maximum}")
        return value

    @staticmethod
    def _validate_names(value: Any, field_name: str, count: int) -> tuple[str, ...]:
        if not isinstance(value, list) or len(value) != count:
            raise MutationValidationError(f"{field_name}: expected exactly {count} names")
        result: list[str] = []
        for item in value:
            if not isinstance(item, str) or not item.strip() or len(item.strip()) > 400:
                raise MutationValidationError(f"{field_name}: every name must be 1..400 chars")
            result.append(item.strip())
        return tuple(result)

    @classmethod
    def _validate_adset_names(
        cls, value: Any, campaign_count: int, adsets_per_campaign: int
    ) -> tuple[tuple[str, ...], ...]:
        if not isinstance(value, list):
            raise MutationValidationError("adset_names: expected a list")
        if len(value) == campaign_count and all(isinstance(item, list) for item in value):
            return tuple(
                cls._validate_names(item, f"adset_names[{index}]", adsets_per_campaign)
                for index, item in enumerate(value)
            )
        total = campaign_count * adsets_per_campaign
        if len(value) == total and all(isinstance(item, str) for item in value):
            flat = cls._validate_names(value, "adset_names", total)
            return tuple(
                flat[index * adsets_per_campaign : (index + 1) * adsets_per_campaign]
                for index in range(campaign_count)
            )
        if len(value) == adsets_per_campaign and all(isinstance(item, str) for item in value):
            shared = cls._validate_names(value, "adset_names", adsets_per_campaign)
            return tuple(shared for _ in range(campaign_count))
        raise MutationValidationError(
            "adset_names: expected nested campaign_count x adsets_per_campaign names, "
            "a flat list for every target, or one reusable per-campaign name list"
        )

    @staticmethod
    def _utcnow() -> datetime:
        return datetime.now(UTC)

    @classmethod
    def _validate_start_time(cls, value: Any) -> tuple[str, datetime]:
        if not isinstance(value, str) or not value.strip():
            raise MutationValidationError("start_time: expected ISO-8601 UTC string")
        cleaned = value.strip()
        parseable = cleaned[:-1] + "+00:00" if cleaned.endswith(("Z", "z")) else cleaned
        try:
            parsed = datetime.fromisoformat(parseable)
        except ValueError as exc:
            raise MutationValidationError("start_time: expected ISO-8601 UTC string") from exc
        if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
            raise MutationValidationError("start_time: timezone must be UTC")
        parsed_utc = parsed.astimezone(UTC)
        if parsed_utc <= cls._utcnow() + _START_TIME_GUARD:
            raise MutationValidationError(
                "start_time: must be at least "
                f"{_START_TIME_GUARD_MINUTES} minutes in the future for crash recovery"
            )
        return cleaned, parsed_utc

    @classmethod
    def _require_activation_headroom(cls, plan: _Plan) -> None:
        if plan.start_at <= cls._utcnow() + _START_TIME_GUARD:
            raise RuntimeError(
                "activation aborted: start_time no longer leaves the required "
                f"{_START_TIME_GUARD_MINUTES}-minute crash-recovery window"
            )

    @staticmethod
    def _parse_graph_time(value: Any) -> datetime | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        if normalized.endswith(("Z", "z")):
            normalized = normalized[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(UTC)


__all__ = [
    "DuplicateAdsetStructureHandler",
    "DuplicateAdsetStructurePartialError",
]
