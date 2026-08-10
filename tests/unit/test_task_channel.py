# -*- coding: utf-8 -*-
"""Pure unit tests for canonical pause/activate task predicates."""

from __future__ import annotations

from core.tasks.channel import (
    disable_channel_sql,
    enable_channel_sql,
    is_disable_row,
    is_enable_row,
    target_id_sql,
)


def test_is_disable_row_matches_only_pause_ad() -> None:
    assert is_disable_row("meta_api_mutation", "pause_ad") is True
    assert is_disable_row("disable", None) is False
    assert is_disable_row("meta_api_mutation", "activate_ad") is False
    assert is_disable_row("enable", None) is False
    assert is_disable_row("meta_api_mutation", None) is False
    assert is_disable_row(None, None) is False


def test_is_enable_row_matches_only_activate_ad() -> None:
    assert is_enable_row("meta_api_mutation", "activate_ad") is True
    assert is_enable_row("enable", None) is False
    assert is_enable_row("meta_api_mutation", "pause_ad") is False
    assert is_enable_row("disable", None) is False
    assert is_enable_row(None, None) is False


def test_disable_channel_sql_shape() -> None:
    sql = disable_channel_sql("tq")
    assert "tq.task_type = 'meta_api_mutation'" in sql
    assert "tq.payload->>'mutation_kind' = 'pause_ad'" in sql
    assert "task_type = 'disable'" not in sql
    assert "tq." in disable_channel_sql()


def test_enable_channel_sql_shape() -> None:
    sql = enable_channel_sql("x")
    assert "x.payload->>'mutation_kind' = 'activate_ad'" in sql
    assert "task_type = 'enable'" not in sql


def test_target_id_sql_is_canonical() -> None:
    sql = target_id_sql("tq")
    assert sql == "tq.payload->>'target_id'"
