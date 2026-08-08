# -*- coding: utf-8 -*-
"""Tasks-домен: unified outbox + enable_recommendation event log."""

from __future__ import annotations

from core.models.tasks.adset_duplicate_preview import AdsetDuplicatePreview
from core.models.tasks.browser_operation_capability_use import (
    BrowserOperationCapabilityUse,
)
from core.models.tasks.browser_operation_lease import BrowserOperationLease
from core.models.tasks.command_receipt import CommandIdempotencyReceipt
from core.models.tasks.enable_recommendation import EnableRecommendation
from core.models.tasks.task_queue import TaskQueue

__all__ = [
    "AdsetDuplicatePreview",
    "BrowserOperationCapabilityUse",
    "BrowserOperationLease",
    "CommandIdempotencyReceipt",
    "EnableRecommendation",
    "TaskQueue",
]
