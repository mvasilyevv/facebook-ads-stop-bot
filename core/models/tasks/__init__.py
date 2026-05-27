# -*- coding: utf-8 -*-
"""Tasks-домен: unified outbox + enable_recommendation event log."""

from __future__ import annotations

from core.models.tasks.enable_recommendation import EnableRecommendation
from core.models.tasks.task_queue import TaskQueue

__all__ = [
    "EnableRecommendation",
    "TaskQueue",
]
