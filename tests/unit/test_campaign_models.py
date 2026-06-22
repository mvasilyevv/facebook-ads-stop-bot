# -*- coding: utf-8 -*-
"""Unit: ORM-модели campaign_preset / campaign_run.

Без БД: проверяем регистрацию в metadata, дефолты статуса/jsonb, состав индексов
и CHECK-констрейнтов. Money-критично: статусный CHECK не должен пропускать левые значения.
"""

from __future__ import annotations

import uuid

from core.models import Base
from core.models.campaigns import CampaignPreset, CampaignRun
from core.models.campaigns.run import CAMPAIGN_RUN_STATUSES


# Обе таблицы зарегистрированы в Base.metadata (нужно для create_all/Alembic autogenerate).
def test_tables_registered_in_metadata() -> None:
    assert "campaign_preset" in Base.metadata.tables
    assert "campaign_run" in Base.metadata.tables


# CampaignPreset конструируется, name обязателен, jsonb/числовые дефолты заданы на уровне сервера.
def test_preset_construct_and_server_defaults() -> None:
    preset = CampaignPreset(
        name="GH_CR default",
        act_id="act_123",
        page_id="111",
        pixel_id="222",
    )
    assert preset.name == "GH_CR default"
    # Серверные дефолты применяются при INSERT, в Python-объекте до flush — None.
    # Проверяем, что server_default объявлен в колонках (money/SOP-дефолты).
    cols = CampaignPreset.__table__.columns
    assert cols["objective"].server_default is not None
    assert cols["optimization_goal"].server_default is not None
    assert cols["custom_event_type"].server_default is not None
    assert cols["special_ad_categories"].server_default is not None
    assert cols["cta"].server_default is not None
    assert cols["text_optimizations"].server_default is not None
    assert cols["click_through_days"].server_default is not None
    assert cols["view_through_days"].server_default is not None


# name пресета уникален (на пресет — стабильный переиспользуемый конфиг).
def test_preset_name_unique() -> None:
    name_col = CampaignPreset.__table__.columns["name"]
    assert name_col.unique is True


# CampaignRun конструируется с обязательным config (снимок CampaignConfig) и статусом.
def test_run_construct() -> None:
    run = CampaignRun(
        config={"offer_code": "GH_CR", "campaigns": []},
        status="queued",
    )
    assert run.status == "queued"
    assert run.config["offer_code"] == "GH_CR"


# preset_id у run — nullable FK (run может быть ad-hoc без пресета).
def test_run_preset_fk_nullable() -> None:
    col = CampaignRun.__table__.columns["preset_id"]
    assert col.nullable is True
    assert len(col.foreign_keys) == 1
    fk = next(iter(col.foreign_keys))
    assert fk.column.table.name == "campaign_preset"


# status имеет server_default 'queued' (свежесозданный run всегда в очереди).
def test_run_status_default_queued() -> None:
    col = CampaignRun.__table__.columns["status"]
    assert col.server_default is not None
    assert "queued" in str(col.server_default.arg)


# Канон допустимых статусов покрывает весь жизненный цикл воркера.
def test_run_status_canon() -> None:
    assert CAMPAIGN_RUN_STATUSES == (
        "queued",
        "uniquifying",
        "uploading",
        "creating",
        "succeeded",
        "failed",
        "cancelled",
    )


# CHECK-констрейнт статуса перечисляет ровно канон (защита от мусора в money-таблице).
def test_run_status_check_constraint_matches_canon() -> None:
    checks = [
        c
        for c in CampaignRun.__table__.constraints
        if c.__class__.__name__ == "CheckConstraint"
        and getattr(c, "name", "") == "ck_campaign_run_status"
    ]
    assert len(checks) == 1
    sqltext = str(checks[0].sqltext)
    for status in CAMPAIGN_RUN_STATUSES:
        assert status in sqltext


# Индексы по status и created_at объявлены (UI-история фильтрует/сортирует по ним).
def test_run_indexes_status_and_created_at() -> None:
    index_names = {ix.name for ix in CampaignRun.__table__.indexes}
    assert "ix_campaign_run_status" in index_names
    assert "ix_campaign_run_created_at" in index_names


# idempotency_key уникален, но nullable (ad-hoc run без ключа допустим).
def test_run_idempotency_key_unique_nullable() -> None:
    col = CampaignRun.__table__.columns["idempotency_key"]
    assert col.nullable is True
    uq = [
        c
        for c in CampaignRun.__table__.constraints
        if c.__class__.__name__ == "UniqueConstraint"
        and "idempotency_key" in [col.name for col in c.columns]
    ]
    assert len(uq) == 1


# progress/created_meta_ids — jsonb с server_default пустого объекта (воркер дописывает инкрементально).
def test_run_jsonb_defaults() -> None:
    cols = CampaignRun.__table__.columns
    assert cols["progress"].server_default is not None
    assert cols["created_meta_ids"].server_default is not None
    assert cols["error"].nullable is True


# PK обеих таблиц — UUID (через mixin UUIDPrimaryKey).
def test_uuid_primary_keys() -> None:
    assert CampaignPreset.__table__.columns["id"].primary_key is True
    assert CampaignRun.__table__.columns["id"].primary_key is True
    # server_default = gen_random_uuid()
    assert "gen_random_uuid" in str(CampaignPreset.__table__.columns["id"].server_default.arg)


# created_by_chat_id опционален в обеих таблицах (источник может быть HTTP без TG-чата).
def test_created_by_chat_id_optional() -> None:
    assert CampaignPreset.__table__.columns["created_by_chat_id"].nullable is True
    assert CampaignRun.__table__.columns["created_by_chat_id"].nullable is True


# Timestamp-mixin даёт created_at/updated_at в обеих таблицах.
def test_timestamp_columns_present() -> None:
    for model in (CampaignPreset, CampaignRun):
        assert "created_at" in model.__table__.columns
        assert "updated_at" in model.__table__.columns


# id — настоящий UUID-тип (Python uuid сериализуется без ошибок при присвоении).
def test_id_accepts_uuid() -> None:
    new_id = uuid.uuid4()
    run = CampaignRun(id=new_id, config={}, status="queued")
    assert run.id == new_id
