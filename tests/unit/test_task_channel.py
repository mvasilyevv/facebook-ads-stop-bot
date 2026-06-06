# -*- coding: utf-8 -*-
"""Unit-тесты предикатов канала отключения/включения (core/tasks/channel.py).

Pure-функции, без БД. Проверяют, что после удаления DOM-канала disable/enable
резолвятся как meta_api_mutation pause_ad/activate_ad с фолбэком на legacy.
"""

from __future__ import annotations

from core.tasks.channel import (
    disable_channel_sql,
    enable_channel_sql,
    is_disable_row,
    is_enable_row,
    target_id_sql,
)


# is_disable_row: новый канал (pause_ad) и legacy disable → True, остальное → False
def test_is_disable_row_matches_pause_ad_and_legacy() -> None:
    assert is_disable_row("meta_api_mutation", "pause_ad") is True
    assert is_disable_row("disable", None) is True
    assert is_disable_row("meta_api_mutation", "activate_ad") is False
    assert is_disable_row("enable", None) is False
    assert is_disable_row("meta_api_mutation", None) is False
    assert is_disable_row(None, None) is False


# is_enable_row: новый канал (activate_ad) и legacy enable → True, остальное → False
def test_is_enable_row_matches_activate_ad_and_legacy() -> None:
    assert is_enable_row("meta_api_mutation", "activate_ad") is True
    assert is_enable_row("enable", None) is True
    assert is_enable_row("meta_api_mutation", "pause_ad") is False
    assert is_enable_row("disable", None) is False
    assert is_enable_row(None, None) is False


# disable_channel_sql: содержит pause_ad + legacy disable + переданный alias
def test_disable_channel_sql_shape() -> None:
    sql = disable_channel_sql("tq")
    assert "tq.task_type = 'meta_api_mutation'" in sql
    assert "tq.payload->>'mutation_kind' = 'pause_ad'" in sql
    assert "tq.task_type = 'disable'" in sql
    # дефолтный alias
    assert "tq." in disable_channel_sql()


# enable_channel_sql: содержит activate_ad + legacy enable
def test_enable_channel_sql_shape() -> None:
    sql = enable_channel_sql("x")
    assert "x.payload->>'mutation_kind' = 'activate_ad'" in sql
    assert "x.task_type = 'enable'" in sql


# target_id_sql: COALESCE(target_id, fb_ad_id) с alias — резолв нового и legacy fb_ad_id
def test_target_id_sql_coalesce() -> None:
    sql = target_id_sql("tq")
    assert sql == "COALESCE(tq.payload->>'target_id', tq.payload->>'fb_ad_id')"
