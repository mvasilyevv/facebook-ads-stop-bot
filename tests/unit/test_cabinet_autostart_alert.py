# -*- coding: utf-8 -*-
"""Autostart notification architecture: one transactional outbox path."""

from __future__ import annotations

import inspect

import apps.cabinet_scheduler.main as cab


def test_post_commit_best_effort_alert_hook_is_removed() -> None:
    assert not hasattr(cab, "_alert_autostart")
    assert "notify_owners" not in inspect.getsource(cab.tick_loop)


def test_started_projection_is_inside_run_transaction() -> None:
    source = inspect.getsource(cab.run_one_tick)
    transaction = source.index("async with engine.begin() as conn:")
    child_insert = source.index("create_mutation_task(", transaction)
    scan_insert = source.index("enqueue_observer_scan(", child_insert)
    notification = source.index("notify_owners_in_transaction(", scan_insert)

    assert transaction < child_insert < scan_insert < notification
    assert 'dedupe_key=f"autostart_alert:{day}:started"' in source


def test_daily_ledger_is_read_only_after_cross_instance_day_lock() -> None:
    source = inspect.getsource(cab.run_one_tick)
    transaction = source.index("async with engine.begin() as conn:")
    day_lock = source.index("pg_advisory_xact_lock(", transaction)
    ledger = source.index("_load_scheduled_autostart_ads(", day_lock)
    target_locks = source.index("for ad_id in pending_ids:", ledger)
    child_insert = source.index("create_mutation_task(", target_locks)

    assert transaction < day_lock < ledger < target_locks < child_insert
