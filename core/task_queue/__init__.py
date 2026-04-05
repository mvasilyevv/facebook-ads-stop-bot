# -*- coding: utf-8 -*-
"""Общая очередь задач и базовый воркер для outbox-паттерна."""

from core.task_queue.base_worker import BaseTaskWorker
from core.task_queue.postgres_queue import PostgresTaskQueue

__all__ = ["BaseTaskWorker", "PostgresTaskQueue"]
