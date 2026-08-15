# -*- coding: utf-8 -*-
"""Семантика точных Meta effective_status без потери исходного кода."""

from __future__ import annotations

DELIVERY_ACTIVE_STATUSES = frozenset({"ACTIVE", "WITH_ISSUES"})
DELIVERY_ACTIVATABLE_STATUSES = frozenset({"OFF", "PAUSED"})
DELIVERY_DISABLED_STATUSES = frozenset(
    {
        "OFF",
        "PAUSED",
        "ADSET_PAUSED",
        "CAMPAIGN_PAUSED",
        "CAMPAIGN_GROUP_PAUSED",
        "ARCHIVED",
        "DELETED",
    }
)
DELIVERY_REJECTED_STATUSES = frozenset({"DISAPPROVED"})
DELIVERY_ATTENTION_STATUSES = frozenset(
    {
        "WITH_ISSUES",
        "PENDING_REVIEW",
        "IN_REVIEW",
        "PREAPPROVED",
        "IN_PROCESS",
        "PROCESSING",
        "PENDING_BILLING_INFO",
        "NOT_DELIVERING",
        "ADSET_PAUSED_NOT_DELIVERING",
    }
)


def normalized_delivery_status(value: str | None) -> str:
    return (value or "").strip().upper()


def is_delivery_active(value: str | None) -> bool:
    return normalized_delivery_status(value) in DELIVERY_ACTIVE_STATUSES


def is_delivery_disabled(value: str | None) -> bool:
    return normalized_delivery_status(value) in DELIVERY_DISABLED_STATUSES


def is_delivery_activatable(value: str | None) -> bool:
    return normalized_delivery_status(value) in DELIVERY_ACTIVATABLE_STATUSES


def is_moderation_rejected(value: str | None) -> bool:
    return normalized_delivery_status(value) in DELIVERY_REJECTED_STATUSES


__all__ = [
    "DELIVERY_ACTIVE_STATUSES",
    "DELIVERY_ACTIVATABLE_STATUSES",
    "DELIVERY_ATTENTION_STATUSES",
    "DELIVERY_DISABLED_STATUSES",
    "DELIVERY_REJECTED_STATUSES",
    "is_delivery_active",
    "is_delivery_activatable",
    "is_delivery_disabled",
    "is_moderation_rejected",
    "normalized_delivery_status",
]
