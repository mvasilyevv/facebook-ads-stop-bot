# -*- coding: utf-8 -*-
"""Alertmanager webhook: authenticate, transact incident/outbox, acknowledge."""

from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Response, status

from apps.api.deps import DepEngine, DepSettings
from core.config import reveal_secret
from core.telegram.alertmanager_ingress import (
    AlertmanagerWebhookPayload,
    persist_alertmanager_payload,
)

router = APIRouter(prefix="/v1/integrations/alertmanager", tags=["alertmanager"])


@router.post(
    "/webhook",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    responses={
        401: {"description": "Invalid Alertmanager webhook secret"},
        503: {"description": "Alertmanager webhook secret is not configured"},
    },
)
async def receive_alertmanager_webhook(
    payload: AlertmanagerWebhookPayload,
    engine: DepEngine,
    settings: DepSettings,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> Response:
    """Return 204 only after incident and notification intent commit together."""
    expected = reveal_secret(settings.alertmanager_webhook_secret).strip()
    if not expected:
        raise HTTPException(status_code=503, detail="Alertmanager webhook is not configured")

    scheme, _, provided = (authorization or "").partition(" ")
    if (
        scheme.lower() != "bearer"
        or not provided
        or not secrets.compare_digest(provided.strip(), expected)
    ):
        raise HTTPException(status_code=401, detail="Invalid Alertmanager webhook secret")

    async with engine.begin() as conn:
        await persist_alertmanager_payload(
            conn,
            payload,
            operator_public_url=settings.frontend_origin or "",
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["receive_alertmanager_webhook", "router"]
