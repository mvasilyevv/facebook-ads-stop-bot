# -*- coding: utf-8 -*-
"""Canonical pause/activate SQL predicates for operator reads.

The only runtime channel is ``task_type='meta_api_mutation'`` with
``mutation_kind='pause_ad'/'activate_ad'``.  The target identifier always lives
in ``payload.target_id``.
"""

from __future__ import annotations

PAUSE_KIND = "pause_ad"
ACTIVATE_KIND = "activate_ad"


def disable_channel_sql(alias: str = "tq") -> str:
    """Return the predicate for a canonical Marketing API pause-ad task."""
    return (
        f"({alias}.task_type = 'meta_api_mutation' "
        f"AND {alias}.payload->>'mutation_kind' = 'pause_ad')"
    )


def enable_channel_sql(alias: str = "tq") -> str:
    """Return the predicate for a canonical Marketing API activate-ad task."""
    return (
        f"({alias}.task_type = 'meta_api_mutation' "
        f"AND {alias}.payload->>'mutation_kind' = 'activate_ad')"
    )


def target_id_sql(alias: str = "tq") -> str:
    """Return the canonical Meta target-id expression."""
    return f"{alias}.payload->>'target_id'"


def is_disable_row(task_type: str | None, mutation_kind: str | None) -> bool:
    """Return whether a queue row is a canonical pause-ad task."""
    return task_type == "meta_api_mutation" and mutation_kind == PAUSE_KIND


def is_enable_row(task_type: str | None, mutation_kind: str | None) -> bool:
    """Return whether a queue row is a canonical activate-ad task."""
    return task_type == "meta_api_mutation" and mutation_kind == ACTIVATE_KIND


__all__ = [
    "ACTIVATE_KIND",
    "PAUSE_KIND",
    "disable_channel_sql",
    "enable_channel_sql",
    "is_disable_row",
    "is_enable_row",
    "target_id_sql",
]
