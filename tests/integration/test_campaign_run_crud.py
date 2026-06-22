# -*- coding: utf-8 -*-
"""Интеграционные тесты CRUD campaign_preset / campaign_run — реальная БД.

Проверяет: server-side дефолты (SOP), UNIQUE name пресета, FK run→preset с SET NULL,
UNIQUE idempotency_key, CHECK status, переходы статусов и jsonb-обновление прогресса.

НЕ гонять на боевой :5433 — нужен изолированный <POSTGRES_DB>_test (фикстура pg_engine).
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from core.models.campaigns import CampaignPreset, CampaignRun


@pytest_asyncio.fixture
async def clean_campaign_tables(pg_engine):
    """Чистит campaign_run/campaign_preset до и после теста (run первым — FK)."""

    async def _truncate():
        async with pg_engine.begin() as conn:
            await conn.execute(text("DELETE FROM campaign_run"))
            await conn.execute(text("DELETE FROM campaign_preset"))

    await _truncate()
    yield
    await _truncate()


def _session_factory(pg_engine):
    return async_sessionmaker(pg_engine, expire_on_commit=False)


def _new_preset(name: str = "GH_CR base") -> CampaignPreset:
    return CampaignPreset(name=name, act_id="act_1", page_id="100", pixel_id="200")


# Пресет создаётся, server-side SOP-дефолты применяются (Purchase-оптимизация и пр.).
@pytest.mark.asyncio
async def test_preset_insert_applies_sop_defaults(pg_engine, clean_campaign_tables) -> None:
    Session = _session_factory(pg_engine)
    async with Session() as s:
        preset = _new_preset()
        s.add(preset)
        await s.commit()
        await s.refresh(preset)

        assert preset.id is not None
        assert preset.objective == "OUTCOME_SALES"
        assert preset.optimization_goal == "OFFSITE_CONVERSIONS"
        assert preset.custom_event_type == "PURCHASE"
        assert preset.special_ad_categories == ["NONE"]
        assert preset.cta == "PLAY_GAME"
        assert preset.text_optimizations == "OPT_OUT"
        assert preset.click_through_days == 1
        assert preset.view_through_days == 1
        assert preset.extra == {}
        assert preset.created_at is not None


# Два пресета с одинаковым name → IntegrityError (UNIQUE).
@pytest.mark.asyncio
async def test_preset_name_unique(pg_engine, clean_campaign_tables) -> None:
    Session = _session_factory(pg_engine)
    async with Session() as s:
        s.add(_new_preset("dup"))
        await s.commit()
    with pytest.raises(IntegrityError):
        async with Session() as s:
            s.add(_new_preset("dup"))
            await s.commit()


# Run создаётся со статусом по умолчанию queued и пустыми jsonb progress/created_meta_ids.
@pytest.mark.asyncio
async def test_run_insert_defaults(pg_engine, clean_campaign_tables) -> None:
    Session = _session_factory(pg_engine)
    async with Session() as s:
        run = CampaignRun(config={"offer_code": "GH_CR", "campaigns": []})
        s.add(run)
        await s.commit()
        await s.refresh(run)

        assert run.status == "queued"
        assert run.progress == {}
        assert run.created_meta_ids == {}
        assert run.error is None
        assert run.preset_id is None
        assert run.config["offer_code"] == "GH_CR"


# Run ссылается на preset; удаление пресета зануляет preset_id (ondelete=SET NULL).
@pytest.mark.asyncio
async def test_run_fk_set_null_on_preset_delete(pg_engine, clean_campaign_tables) -> None:
    Session = _session_factory(pg_engine)
    async with Session() as s:
        preset = _new_preset("for-fk")
        s.add(preset)
        await s.commit()
        await s.refresh(preset)
        preset_id = preset.id

        run = CampaignRun(config={}, preset_id=preset_id)
        s.add(run)
        await s.commit()
        await s.refresh(run)
        run_id = run.id
        assert run.preset_id == preset_id

    async with pg_engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM campaign_preset WHERE id = :pid"), {"pid": str(preset_id)}
        )

    async with Session() as s:
        reloaded = await s.get(CampaignRun, run_id)
        assert reloaded is not None
        assert reloaded.preset_id is None


# Дублирующийся idempotency_key → IntegrityError (защита от двойного залива).
@pytest.mark.asyncio
async def test_run_idempotency_key_unique(pg_engine, clean_campaign_tables) -> None:
    key = f"idem-{uuid.uuid4().hex[:8]}"
    Session = _session_factory(pg_engine)
    async with Session() as s:
        s.add(CampaignRun(config={}, idempotency_key=key))
        await s.commit()
    with pytest.raises(IntegrityError):
        async with Session() as s:
            s.add(CampaignRun(config={}, idempotency_key=key))
            await s.commit()


# Несколько run с NULL idempotency_key допустимы (NULL не нарушает UNIQUE в Postgres).
@pytest.mark.asyncio
async def test_run_null_idempotency_allows_many(pg_engine, clean_campaign_tables) -> None:
    Session = _session_factory(pg_engine)
    async with Session() as s:
        s.add(CampaignRun(config={}))
        s.add(CampaignRun(config={}))
        await s.commit()
        count = await s.scalar(text("SELECT count(*) FROM campaign_run"))
        assert count == 2


# Левый статус отвергается CHECK-констрейнтом (money-таблица не должна копить мусор).
@pytest.mark.asyncio
async def test_run_invalid_status_rejected(pg_engine, clean_campaign_tables) -> None:
    Session = _session_factory(pg_engine)
    with pytest.raises(IntegrityError):
        async with Session() as s:
            s.add(CampaignRun(config={}, status="hacking"))
            await s.commit()


# Прогресс/статус обновляются инкрементально (имитация работы воркера).
@pytest.mark.asyncio
async def test_run_status_progression(pg_engine, clean_campaign_tables) -> None:
    Session = _session_factory(pg_engine)
    async with Session() as s:
        run = CampaignRun(config={"offer_code": "GH_CR"})
        s.add(run)
        await s.commit()
        await s.refresh(run)
        run_id = run.id

    for status in ("uniquifying", "uploading", "creating", "succeeded"):
        async with Session() as s:
            run = await s.get(CampaignRun, run_id)
            run.status = status
            run.progress = {"stage": status, "done": status == "succeeded"}
            if status == "succeeded":
                run.created_meta_ids = {"campaign_id": "120", "adset_ids": ["1", "2"]}
            await s.commit()

    async with Session() as s:
        final = await s.get(CampaignRun, run_id)
        assert final.status == "succeeded"
        assert final.progress["stage"] == "succeeded"
        assert final.created_meta_ids["campaign_id"] == "120"
