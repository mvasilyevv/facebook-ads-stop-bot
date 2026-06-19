# -*- coding: utf-8 -*-
"""Юнит-тест money-гарда: пустой allowlist (single-cab) = ничего не отслеживаем."""

from __future__ import annotations

from apps.observer_worker.main import _allowlist_blocks_scan


# Один кабинет + пустой allowlist → скан блокируется (opt-in мониторинг)
def test_single_cab_empty_allowlist_blocks() -> None:
    assert _allowlist_blocks_scan(single_cabinet=True, campaign_ids=[]) is True


# Один кабинет + выбраны кампании → скан идёт
def test_single_cab_with_campaigns_scans() -> None:
    assert _allowlist_blocks_scan(single_cabinet=True, campaign_ids=["123"]) is False


# Мульти-каб (allowlist неприменим) → НЕ блокируем даже при пустом списке
def test_multi_cab_does_not_block() -> None:
    assert _allowlist_blocks_scan(single_cabinet=False, campaign_ids=[]) is False
    assert _allowlist_blocks_scan(single_cabinet=False, campaign_ids=["123"]) is False
