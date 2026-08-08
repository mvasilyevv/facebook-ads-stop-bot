# -*- coding: utf-8 -*-
"""Интеграционные тесты persist-функций FSM-reset из core/observer/writers.py.

`reset_alert_state_after_disable_succeeded` / `reset_alert_state_after_enable_succeeded`
вызываются из core.meta_api.fsm_sync.sync_fsm_after_mutation после успешного
mark_task_succeeded (DOM-toggle канал удалён, toggle_executor больше не существует).
Тестируем что UPDATE действительно переводит state в нужное значение,
идемпотентен и защищён от misuse.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text

from core.observer.writers import (
    mark_alert_state_claimed,
    reset_alert_state_after_disable_succeeded,
    reset_alert_state_after_enable_succeeded,
)


@pytest_asyncio.fixture
async def ad_with_state(pg_engine):
    """Создаёт offer→campaign→adset→ad + ad_alert_state в произвольном начальном state.

    Возвращает fabric (initial_state) → fb_ad_id с уже проинициализированным состоянием.
    Teardown — каскадно по offers.
    """
    suffix = uuid.uuid4().hex[:8]
    offer_id = uuid.uuid4()
    campaign_id = uuid.uuid4()
    adset_id = uuid.uuid4()
    ad_id = uuid.uuid4()
    fb_ad_id = f"230099{suffix}"
    alert_state_id = uuid.uuid4()

    async with pg_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO offers (id, code, name) VALUES (:i, :c, :n)"),
            {"i": offer_id, "c": f"RST_{suffix}", "n": f"Reset offer {suffix}"},
        )
        await conn.execute(
            text(
                "INSERT INTO fb_campaigns (id, campaign_name, offer_id, ad_account_id) VALUES (:i, :n, :o, '123')"
            ),
            {"i": campaign_id, "n": f"CMP_{suffix}", "o": offer_id},
        )
        await conn.execute(
            text("INSERT INTO fb_adsets (id, campaign_id, adset_name) VALUES (:i, :c, :n)"),
            {"i": adset_id, "c": campaign_id, "n": f"ADSET_{suffix}"},
        )
        await conn.execute(
            text("INSERT INTO fb_ads (id, adset_id, fb_ad_id, ad_name) VALUES (:i, :a, :f, :n)"),
            {"i": ad_id, "a": adset_id, "f": fb_ad_id, "n": f"AD_{suffix}"},
        )

    async def _seed(initial_state: str, *, with_token: bool = True) -> str:
        token = uuid.uuid4() if with_token else None
        stage = (
            "warning"
            if initial_state == "warning_sent"
            else ("stop" if initial_state in ("stop_sent", "claimed") else None)
        )
        async with pg_engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM ad_alert_state WHERE ad_id = :aid"),
                {"aid": ad_id},
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO ad_alert_state
                        (id, ad_id, alert_state, current_stage, open_state_token,
                         warning_rule_codes, stop_rule_codes, last_transition_at)
                    VALUES
                        (:id, :aid, :st, :stg, :tok,
                         '["w1"]'::jsonb, '["s1"]'::jsonb, NOW() - INTERVAL '1 hour')
                    """
                ),
                {
                    "id": alert_state_id,
                    "aid": ad_id,
                    "st": initial_state,
                    "stg": stage,
                    "tok": token,
                },
            )
        return fb_ad_id

    yield _seed

    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM offers WHERE id = :i"), {"i": offer_id})


async def _read_state(pg_engine, fb_ad_id: str) -> dict:
    """Читает текущее состояние ad_alert_state для конкретного объявления."""
    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT s.alert_state, s.current_stage, s.open_state_token,
                           s.warning_rule_codes, s.stop_rule_codes,
                           s.last_transition_at, s.updated_at
                    FROM ad_alert_state s
                    JOIN fb_ads a ON a.id = s.ad_id
                    WHERE a.fb_ad_id = :fbid
                    """
                ),
                {"fbid": fb_ad_id},
            )
        ).first()
    return {
        "alert_state": row[0],
        "current_stage": row[1],
        "open_state_token": row[2],
        "warning_rule_codes": row[3],
        "stop_rule_codes": row[4],
        "last_transition_at": row[5],
        "updated_at": row[6],
    }


# Сценарий: stop_sent → disabled, last_transition_at обновляется
@pytest.mark.asyncio
async def test_disable_reset_stop_sent_to_disabled(pg_engine, ad_with_state) -> None:
    fb_ad_id = await ad_with_state("stop_sent")
    before = await _read_state(pg_engine, fb_ad_id)

    changed = await reset_alert_state_after_disable_succeeded(pg_engine, fb_ad_id=fb_ad_id)
    assert changed is True

    after = await _read_state(pg_engine, fb_ad_id)
    assert after["alert_state"] == "disabled"
    assert after["last_transition_at"] > before["last_transition_at"]


# Сценарий: claimed → disabled (валидный transition по FSM)
@pytest.mark.asyncio
async def test_disable_reset_claimed_to_disabled(pg_engine, ad_with_state) -> None:
    fb_ad_id = await ad_with_state("claimed")
    changed = await reset_alert_state_after_disable_succeeded(pg_engine, fb_ad_id=fb_ad_id)
    assert changed is True
    after = await _read_state(pg_engine, fb_ad_id)
    assert after["alert_state"] == "disabled"


# Сценарий: warning_sent → disabled (manual disable из WARNING-алерта через TG)
@pytest.mark.asyncio
async def test_disable_reset_warning_sent_to_disabled(pg_engine, ad_with_state) -> None:
    fb_ad_id = await ad_with_state("warning_sent")
    changed = await reset_alert_state_after_disable_succeeded(pg_engine, fb_ad_id=fb_ad_id)
    assert changed is True
    after = await _read_state(pg_engine, fb_ad_id)
    assert after["alert_state"] == "disabled"


# Сценарий: идемпотентность — повторный вызов на уже disabled → no-op
@pytest.mark.asyncio
async def test_disable_reset_idempotent_when_already_disabled(pg_engine, ad_with_state) -> None:
    fb_ad_id = await ad_with_state("disabled")
    before = await _read_state(pg_engine, fb_ad_id)

    changed = await reset_alert_state_after_disable_succeeded(pg_engine, fb_ad_id=fb_ad_id)
    assert changed is False

    after = await _read_state(pg_engine, fb_ad_id)
    assert after["alert_state"] == "disabled"
    # last_transition_at не должен обновиться — это бы означало повторный transition
    assert after["last_transition_at"] == before["last_transition_at"]


# Сценарий: защита от misuse — normal не возвращаем в disabled
# (observer мог сбросить state из stop_sent → normal после реактивации, его решение приоритетнее)
@pytest.mark.asyncio
async def test_disable_reset_does_not_force_normal_into_disabled(pg_engine, ad_with_state) -> None:
    fb_ad_id = await ad_with_state("normal")
    changed = await reset_alert_state_after_disable_succeeded(pg_engine, fb_ad_id=fb_ad_id)
    assert changed is False
    after = await _read_state(pg_engine, fb_ad_id)
    assert after["alert_state"] == "normal"


# Сценарий: несуществующий fb_ad_id → no-op без исключений
@pytest.mark.asyncio
async def test_disable_reset_unknown_fb_ad_id_is_noop(pg_engine) -> None:
    changed = await reset_alert_state_after_disable_succeeded(pg_engine, fb_ad_id="999999000000000")
    assert changed is False


# Сценарий: enable из disabled → normal + полный сброс контекста FSM
@pytest.mark.asyncio
async def test_enable_reset_disabled_to_normal_clears_context(pg_engine, ad_with_state) -> None:
    fb_ad_id = await ad_with_state("disabled")
    before = await _read_state(pg_engine, fb_ad_id)
    assert before["open_state_token"] is not None  # был открытый инцидент

    changed = await reset_alert_state_after_enable_succeeded(pg_engine, fb_ad_id=fb_ad_id)
    assert changed is True

    after = await _read_state(pg_engine, fb_ad_id)
    assert after["alert_state"] == "normal"
    assert after["current_stage"] is None
    assert after["open_state_token"] is None
    assert after["warning_rule_codes"] == []
    assert after["stop_rule_codes"] == []


# Сценарий: enable из stop_sent → normal (например, пользователь нажал «включить» до auto-disable)
@pytest.mark.asyncio
async def test_enable_reset_stop_sent_to_normal(pg_engine, ad_with_state) -> None:
    fb_ad_id = await ad_with_state("stop_sent")
    changed = await reset_alert_state_after_enable_succeeded(pg_engine, fb_ad_id=fb_ad_id)
    assert changed is True
    after = await _read_state(pg_engine, fb_ad_id)
    assert after["alert_state"] == "normal"


# Сценарий: идемпотентность — повторный enable на уже normal → no-op
@pytest.mark.asyncio
async def test_enable_reset_idempotent_when_already_normal(pg_engine, ad_with_state) -> None:
    fb_ad_id = await ad_with_state("normal")
    before = await _read_state(pg_engine, fb_ad_id)

    changed = await reset_alert_state_after_enable_succeeded(pg_engine, fb_ad_id=fb_ad_id)
    assert changed is False

    after = await _read_state(pg_engine, fb_ad_id)
    assert after["alert_state"] == "normal"
    assert after["last_transition_at"] == before["last_transition_at"]


# Сценарий: несуществующий fb_ad_id для enable → no-op
@pytest.mark.asyncio
async def test_enable_reset_unknown_fb_ad_id_is_noop(pg_engine) -> None:
    changed = await reset_alert_state_after_enable_succeeded(pg_engine, fb_ad_id="999999000000001")
    assert changed is False


# ====================== L2: mark_alert_state_claimed (ручной dis → claimed) ======================


# Сценарий: stop_sent → claimed (юзер нажал dis на STOP-алерте, взял управление)
@pytest.mark.asyncio
async def test_claim_stop_sent_to_claimed(pg_engine, ad_with_state) -> None:
    fb_ad_id = await ad_with_state("stop_sent")
    before = await _read_state(pg_engine, fb_ad_id)

    changed = await mark_alert_state_claimed(pg_engine, fb_ad_id=fb_ad_id)
    assert changed is True

    after = await _read_state(pg_engine, fb_ad_id)
    assert after["alert_state"] == "claimed"
    assert after["last_transition_at"] > before["last_transition_at"]


# Сценарий: warning_sent → claimed (dis из WARNING-алерта)
@pytest.mark.asyncio
async def test_claim_warning_sent_to_claimed(pg_engine, ad_with_state) -> None:
    fb_ad_id = await ad_with_state("warning_sent")
    changed = await mark_alert_state_claimed(pg_engine, fb_ad_id=fb_ad_id)
    assert changed is True
    after = await _read_state(pg_engine, fb_ad_id)
    assert after["alert_state"] == "claimed"


# Сценарий КЛЮЧЕВОЙ (money-blind guard): normal НЕ переводим в claimed —
# иначе ад залип бы в claimed без инцидента, а observer-reopen покрывает только disabled.
@pytest.mark.asyncio
async def test_claim_does_not_force_normal_into_claimed(pg_engine, ad_with_state) -> None:
    fb_ad_id = await ad_with_state("normal")
    changed = await mark_alert_state_claimed(pg_engine, fb_ad_id=fb_ad_id)
    assert changed is False
    after = await _read_state(pg_engine, fb_ad_id)
    assert after["alert_state"] == "normal"


# Сценарий: идемпотентность — повторный claim на уже claimed → no-op
@pytest.mark.asyncio
async def test_claim_idempotent_when_already_claimed(pg_engine, ad_with_state) -> None:
    fb_ad_id = await ad_with_state("claimed")
    before = await _read_state(pg_engine, fb_ad_id)

    changed = await mark_alert_state_claimed(pg_engine, fb_ad_id=fb_ad_id)
    assert changed is False

    after = await _read_state(pg_engine, fb_ad_id)
    assert after["alert_state"] == "claimed"
    assert after["last_transition_at"] == before["last_transition_at"]


# Сценарий: disabled НЕ откатываем в claimed (уже терминально обработан)
@pytest.mark.asyncio
async def test_claim_does_not_revert_disabled(pg_engine, ad_with_state) -> None:
    fb_ad_id = await ad_with_state("disabled")
    changed = await mark_alert_state_claimed(pg_engine, fb_ad_id=fb_ad_id)
    assert changed is False
    after = await _read_state(pg_engine, fb_ad_id)
    assert after["alert_state"] == "disabled"


# Сценарий: несуществующий fb_ad_id → no-op без исключений
@pytest.mark.asyncio
async def test_claim_unknown_fb_ad_id_is_noop(pg_engine) -> None:
    changed = await mark_alert_state_claimed(pg_engine, fb_ad_id="999999000000002")
    assert changed is False
