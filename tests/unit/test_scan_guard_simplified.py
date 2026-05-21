# -*- coding: utf-8 -*-
"""Упрощённый ZeroScanGuard: возвращает причину skip enum'ом, без cabinet_day."""

from __future__ import annotations

from datetime import UTC, datetime

from core.observer.scan_guard import GuardSkipReason, ZeroScanGuard


def _row(fb_ad_id: str, spend: str = "0") -> dict:
    """Минимальный snapshot-словарь для guard'а."""
    return {
        "fb_ad_id": fb_ad_id,
        "spend": spend,
        "clicks": 0,
        "leads": 0,
        "registrations": 0,
        "deposits": 0,
        "last_observed_at": datetime.now(UTC),
    }


# Первый пустой батч — guard пропускает с причиной ZERO_SCAN_PENDING.
def test_first_zero_scan_returns_pending_reason():
    guard = ZeroScanGuard()
    reason = guard.should_skip([_row(f"ad{i}") for i in range(5)])
    assert reason == GuardSkipReason.ZERO_SCAN_PENDING


# Повторный пустой батч — guard принимает (None).
def test_second_zero_scan_accepted():
    guard = ZeroScanGuard()
    guard.should_skip([_row(f"ad{i}") for i in range(5)])
    reason = guard.should_skip([_row(f"ad{i}") for i in range(5)])
    assert reason is None


# Нормальный батч с метриками — guard принимает сразу.
def test_normal_batch_accepted():
    guard = ZeroScanGuard()
    guard.initialize_from_count(40)
    rows = [_row(f"ad{i}", spend="10.50") for i in range(40)]
    reason = guard.should_skip(rows)
    assert reason is None


# Резкое сжатие батча — pending partial.
def test_partial_batch_returns_pending_reason():
    guard = ZeroScanGuard()
    guard.initialize_from_count(40)
    guard.should_skip([_row(f"ad{i}", spend="10.0") for i in range(40)])
    reason = guard.should_skip([_row(f"ad{i}", spend="10.0") for i in range(20)])
    assert reason == GuardSkipReason.PARTIAL_BATCH_PENDING


# Подтверждённое сжатие — принимаем урезанный срез.
def test_partial_batch_second_attempt_accepted():
    guard = ZeroScanGuard()
    guard.initialize_from_count(40)
    guard.should_skip([_row(f"ad{i}", spend="10.0") for i in range(40)])
    guard.should_skip([_row(f"ad{i}", spend="10.0") for i in range(20)])
    reason = guard.should_skip([_row(f"ad{i}", spend="10.0") for i in range(20)])
    assert reason is None


# Пустой список — нет данных, ничего не пропускаем (None).
def test_empty_input_returns_none():
    guard = ZeroScanGuard()
    assert guard.should_skip([]) is None
