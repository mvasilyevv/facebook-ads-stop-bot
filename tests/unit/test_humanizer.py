# -*- coding: utf-8 -*-
"""Тесты для humanizer — задержки в нужном диапазоне."""

from __future__ import annotations

import asyncio
import time

import pytest

from core.campaign_creator.humanizer import human_wait


@pytest.mark.asyncio
async def test_human_wait_within_range():
    """human_wait должен спать в указанном диапазоне (с допуском)."""
    start = time.monotonic()
    await human_wait(100, 200)
    elapsed_ms = (time.monotonic() - start) * 1000
    assert 80 <= elapsed_ms <= 350


@pytest.mark.asyncio
async def test_human_wait_default_range():
    """Дефолтный диапазон 80-300 мс."""
    start = time.monotonic()
    await human_wait()
    elapsed_ms = (time.monotonic() - start) * 1000
    assert 60 <= elapsed_ms <= 400


@pytest.mark.asyncio
async def test_human_wait_randomized():
    """Несколько вызовов дают разные задержки (не константа)."""
    durations = []
    for _ in range(5):
        start = time.monotonic()
        await human_wait(80, 300)
        durations.append(time.monotonic() - start)
    assert len(set(durations)) > 1


def test_human_wait_is_async():
    """human_wait — корутина."""
    assert asyncio.iscoroutinefunction(human_wait)
