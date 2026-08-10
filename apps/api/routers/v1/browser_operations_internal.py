"""Narrow browser-only durable capability consume boundary."""

from __future__ import annotations

import os
import secrets
import uuid
from typing import Literal

from fastapi import APIRouter, Header, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field

from apps.api.deps import DepEngine
from core.meta_api.operation_authority import (
    BrowserCapabilityAuthorityUnavailableError,
    BrowserCapabilityConsume,
    BrowserCapabilityConsumeDeniedError,
    consume_pending_browser_capability,
)
from core.tasks.browser_fence import (
    BrowserMaintenanceCapabilityAuthorityUnavailableError,
    BrowserMaintenanceCapabilityConsume,
    BrowserMaintenanceCapabilityConsumeDeniedError,
    consume_browser_maintenance_capability,
)

router = APIRouter(
    prefix="/v1/internal",
    tags=["internal-browser-operations"],
)


class BrowserCapabilityConsumeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    browser_contract_version: Literal[5]
    rpc: Literal["execute_graph_call", "upload_image", "upload_video"]
    operation: str = Field(min_length=1, max_length=1024)
    session_id: str = Field(min_length=1, max_length=128)
    vision_profile_id: str = Field(min_length=1, max_length=128)
    ad_account_id: str = Field(pattern=r"^[0-9]+$", max_length=32)
    authorized_caller: Literal["autopause", "meta_api", "campaign_creator"]
    task_id: int = Field(gt=0)
    lease_owner: uuid.UUID
    lease_token: int = Field(gt=0)
    capability_expires_at: int = Field(gt=0)
    capability_nonce: str = Field(pattern=r"^[0-9a-f]{32}$")


class BrowserMaintenanceCapabilityConsumeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rpc: Literal["recover_browser_profile"]
    vision_profile_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[^\r\n]+$",
    )
    maintenance_owner: str = Field(pattern=r"^[0-9a-f]{32}$")
    capability_expires_at: int = Field(gt=0)
    capability_nonce: str = Field(pattern=r"^[0-9a-f]{32}$")


def _authorize_browser_consumer(provided: str | None) -> None:
    expected = os.environ.get("BROWSER_AUTHORITY_CONSUMER_TOKEN", "")
    if len(expected) < 48:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="browser authority consumer credential is unavailable",
        )
    candidate = provided or ""
    if not secrets.compare_digest(candidate, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="browser authority consumer credential is invalid",
        )


@router.post(
    "/browser-operations/consume",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def consume_browser_operation(
    body: BrowserCapabilityConsumeRequest,
    engine: DepEngine,
    x_browser_authority_token: str | None = Header(default=None),
) -> Response:
    _authorize_browser_consumer(x_browser_authority_token)
    capability = BrowserCapabilityConsume(
        browser_contract_version=body.browser_contract_version,
        rpc=body.rpc,
        operation=body.operation,
        session_id=body.session_id,
        vision_profile_id=body.vision_profile_id,
        ad_account_id=body.ad_account_id,
        caller=body.authorized_caller,
        task_id=body.task_id,
        lease_owner=body.lease_owner,
        lease_token=body.lease_token,
        expires_at_epoch=body.capability_expires_at,
        nonce=body.capability_nonce,
    )
    try:
        await consume_pending_browser_capability(engine, capability)
    except BrowserCapabilityConsumeDeniedError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="browser capability is not consumable",
        ) from exc
    except BrowserCapabilityAuthorityUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="browser capability authority is unavailable",
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/browser-maintenance/consume",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def consume_browser_maintenance(
    body: BrowserMaintenanceCapabilityConsumeRequest,
    engine: DepEngine,
    x_browser_authority_token: str | None = Header(default=None),
) -> Response:
    _authorize_browser_consumer(x_browser_authority_token)
    capability = BrowserMaintenanceCapabilityConsume(
        profile_id=body.vision_profile_id,
        owner=body.maintenance_owner,
        expires_at_epoch=body.capability_expires_at,
        nonce=body.capability_nonce,
    )
    try:
        # This helper returns only after the transaction commits. Therefore 204
        # is the sole durable boundary that permits browser-agent to mutate
        # Vision lifecycle state.
        await consume_browser_maintenance_capability(engine, capability)
    except BrowserMaintenanceCapabilityConsumeDeniedError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="browser maintenance capability is not consumable",
        ) from exc
    except BrowserMaintenanceCapabilityAuthorityUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="browser maintenance capability authority is unavailable",
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["router"]
