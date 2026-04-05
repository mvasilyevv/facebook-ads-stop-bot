# -*- coding: utf-8 -*-
"""Тесты структуры моделей: индексы, поля upsert-батча, дубликаты."""

from __future__ import annotations

import dataclasses

# --- B1: Проверяем, что в ScannedAdRow нет фантомных полей, которые ломали batch upsert ---


# Проверяем, что поля outbound_clicks/outbound_ctr/landing_page_views/cost_per_landing_page_view
# присутствуют в ScannedAdRow — необходимы для ранних сигналов воронки.
def test_scanned_ad_row_has_outbound_and_lpv_fields():
    from core.scanner.models import ScannedAdRow

    field_names = {f.name for f in dataclasses.fields(ScannedAdRow)}
    required = {
        "outbound_clicks",
        "outbound_ctr",
        "landing_page_views",
        "cost_per_landing_page_view",
    }
    assert required <= field_names, (
        f"Обязательные поля отсутствуют в ScannedAdRow: {required - field_names}"
    )


# Проверяем, что ScannedAdRow является frozen dataclass (неизменяемой).
def test_scanned_ad_row_is_frozen():
    from core.scanner.models import ScannedAdRow

    assert ScannedAdRow.__dataclass_params__.frozen is True


# --- Индексы ad_snapshots: составные именованные индексы покрывают нужные столбцы ---


# Проверяем, что таблица ad_snapshots имеет уникальный индекс по fb_ad_id.
def test_ad_snapshots_unique_index_on_fb_ad_id():
    from core.models import AdSnapshot

    table = AdSnapshot.__table__
    unique_indexes = [idx for idx in table.indexes if idx.unique]
    fb_ad_unique = next(
        (idx for idx in unique_indexes if set(c.name for c in idx.columns) == {"fb_ad_id"}),
        None,
    )
    assert fb_ad_unique is not None, "Уникальный индекс по fb_ad_id не найден в ad_snapshots"


# Проверяем наличие индекса по ad_id в ad_snapshots (FK на fb_ads после нормализации).
def test_ad_snapshots_ad_id_index():
    from core.models import AdSnapshot

    table = AdSnapshot.__table__
    cols_sets = [frozenset(c.name for c in idx.columns) for idx in table.indexes]
    has_ad_id_index = any("ad_id" in cs for cs in cols_sets)
    assert has_ad_id_index, "Индекс по ad_id не найден в ad_snapshots"


# Проверяем наличие составного индекса (last_observed_at, alert_state) в ad_snapshots.
def test_ad_snapshots_composite_last_observed_index():
    from core.models import AdSnapshot

    table = AdSnapshot.__table__
    cols_sets = [frozenset(c.name for c in idx.columns) for idx in table.indexes]
    assert frozenset({"last_observed_at", "alert_state"}) in cols_sets, (
        "Составной индекс (last_observed_at, alert_state) не найден в ad_snapshots"
    )


# Проверяем, что в ad_snapshots НЕТ избыточного одноколоночного индекса по alert_state
# (он дублировал бы составной ix_ad_snapshot_alert_state).
def test_ad_snapshots_no_duplicate_single_alert_state_index():
    from core.models import AdSnapshot

    table = AdSnapshot.__table__
    # Ищем ОДНОКОЛОНОЧНЫЕ неуникальные индексы по alert_state
    single_alert_indexes = [
        idx
        for idx in table.indexes
        if not idx.unique and list(c.name for c in idx.columns) == ["alert_state"]
    ]
    assert len(single_alert_indexes) <= 1, (
        f"Найдено {len(single_alert_indexes)} одноколоночных индекса по alert_state — "
        "должен быть максимум один (именованный)"
    )


# --- Индексы disable_tasks: составные индексы для очереди воркера ---


# Проверяем наличие составного индекса (status, next_retry_at) в disable_tasks.
def test_disable_tasks_queue_composite_index():
    from core.models import DisableTask

    table = DisableTask.__table__
    cols_sets = [frozenset(c.name for c in idx.columns) for idx in table.indexes]
    assert frozenset({"status", "next_retry_at"}) in cols_sets, (
        "Составной индекс (status, next_retry_at) не найден в disable_tasks"
    )


# Проверяем наличие составного индекса (ad_id, open_state_token) в disable_tasks.
def test_disable_tasks_ad_incident_composite_index():
    from core.models import DisableTask

    table = DisableTask.__table__
    cols_sets = [frozenset(c.name for c in idx.columns) for idx in table.indexes]
    assert frozenset({"ad_id", "open_state_token"}) in cols_sets, (
        "Составной индекс (ad_id, open_state_token) не найден в disable_tasks"
    )


# --- Индексы enable_tasks: составной индекс очереди ---


# Проверяем наличие составного индекса (status, next_retry_at) в enable_tasks.
def test_enable_tasks_queue_composite_index():
    from core.models import EnableTask

    table = EnableTask.__table__
    cols_sets = [frozenset(c.name for c in idx.columns) for idx in table.indexes]
    assert frozenset({"status", "next_retry_at"}) in cols_sets, (
        "Составной индекс (status, next_retry_at) не найден в enable_tasks"
    )
