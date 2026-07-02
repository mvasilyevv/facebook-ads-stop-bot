# -*- coding: utf-8 -*-
"""Unit-тесты core.meta_api.queue — pure-функция default_idempotency_key + salt-uuid."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.meta_api.queue import create_draft_task, default_idempotency_key
from core.meta_api.schemas import MetaMutationPayload


# Одинаковые payload + requested_by + (нет salt) → одинаковый ключ (для дедупа).
def test_idempotency_key_stable() -> None:
    payload_a = MetaMutationPayload(
        mutation_kind="pause_ad",
        target_id="ad_1",
        params={"reason": "manual"},
    )
    payload_b = MetaMutationPayload(
        mutation_kind="pause_ad",
        target_id="ad_1",
        params={"reason": "manual"},
    )
    key_a = default_idempotency_key(payload_a, requested_by="user_42")
    key_b = default_idempotency_key(payload_b, requested_by="user_42")
    assert key_a == key_b


# Разный requested_by → разные ключи (auto-bot и user не должны конфликтовать).
def test_idempotency_key_diff_user() -> None:
    payload = MetaMutationPayload(mutation_kind="pause_ad", target_id="ad_1")
    key_user = default_idempotency_key(payload, requested_by="user_42")
    key_bot = default_idempotency_key(payload, requested_by="bot_auto")
    assert key_user != key_bot


# Salt делает каждый ключ уникальным (для DRAFT — каждый новый draft).
def test_idempotency_key_with_salt() -> None:
    payload = MetaMutationPayload(mutation_kind="pause_ad", target_id="ad_1")
    key_1 = default_idempotency_key(payload, requested_by="ai", salt="2026-05-27T10:00:00")
    key_2 = default_idempotency_key(payload, requested_by="ai", salt="2026-05-27T10:01:00")
    assert key_1 != key_2


# Ключ влезает в VARCHAR(128) ограничение БД.
def test_idempotency_key_length() -> None:
    payload = MetaMutationPayload(
        mutation_kind="set_adset_budget",
        target_id="120999888777666",
        params={"daily_budget_cents": 5000, "currency": "USD"},
    )
    key = default_idempotency_key(payload, requested_by="ai_assistant")
    assert len(key) <= 128


# Префикс meta: + mutation_kind + target_id виден в ключе — удобно искать в БД.
def test_idempotency_key_has_prefix() -> None:
    payload = MetaMutationPayload(mutation_kind="pause_ad", target_id="ad_999")
    key = default_idempotency_key(payload, requested_by="user")
    assert key.startswith("meta:pause_ad:ad_999:")


# Разный params → разный ключ.
def test_idempotency_key_params_matter() -> None:
    a = MetaMutationPayload(
        mutation_kind="set_adset_budget", target_id="as_1", params={"daily": 1000}
    )
    b = MetaMutationPayload(
        mutation_kind="set_adset_budget", target_id="as_1", params={"daily": 2000}
    )
    key_a = default_idempotency_key(a, requested_by="ai")
    key_b = default_idempotency_key(b, requested_by="ai")
    assert key_a != key_b


# ====================== MID-5: draft salt = timestamp + uuid4 (без коллизий) ======================


# MID-5: два create_draft_task в одну и ту же секунду (замороженное время) → РАЗНЫЕ
# idempotency_key. Раньше salt=isoformat → одинаковый ISO при двойном клике →
# одинаковый ключ → ON CONFLICT DO NOTHING глотал второй draft. uuid4 разводит ключи.
@pytest.mark.asyncio
async def test_draft_salt_unique_on_same_second_double_click() -> None:
    payload = MetaMutationPayload(mutation_kind="pause_ad", target_id="ad_777")

    captured_keys: list[str] = []

    async def fake_create_task(engine, **kwargs):
        captured_keys.append(kwargs["idempotency_key"])
        return len(captured_keys)  # уникальный id, не None

    # Замораживаем время на одну и ту же секунду для обоих вызовов — эмулируем
    # двойной клик в пределах одной секунды (worst case для salt=isoformat).
    frozen = datetime(2026, 7, 2, 12, 0, 0, tzinfo=timezone.utc)
    frozen_dt = MagicMock()
    frozen_dt.now = MagicMock(return_value=frozen)

    with (
        patch("core.meta_api.queue.create_task", fake_create_task),
        patch("core.meta_api.queue.datetime", frozen_dt),
    ):
        engine = AsyncMock()
        await create_draft_task(engine, payload=payload, requested_by="ai")
        await create_draft_task(engine, payload=payload, requested_by="ai")

    assert len(captured_keys) == 2
    # Ключевой инвариант MID-5: даже при идентичном timestamp ключи РАЗНЫЕ (uuid4-компонент).
    assert captured_keys[0] != captured_keys[1], (
        "два draft в одну секунду дали одинаковый idempotency_key — коллизия MID-5 не устранена"
    )


# MID-5: salt всё ещё содержит timestamp (для читаемости/дебага), но детерминизм
# сломан uuid4 — тот же payload+requested_by даёт разные ключи на каждом вызове.
@pytest.mark.asyncio
async def test_draft_salt_nondeterministic_across_calls() -> None:
    payload = MetaMutationPayload(mutation_kind="activate_ad", target_id="ad_1")
    captured_keys: list[str] = []

    async def fake_create_task(engine, **kwargs):
        captured_keys.append(kwargs["idempotency_key"])
        return len(captured_keys)

    with patch("core.meta_api.queue.create_task", fake_create_task):
        engine = AsyncMock()
        for _ in range(5):
            await create_draft_task(engine, payload=payload, requested_by="ai")

    # Все 5 ключей уникальны — draft'ы не глотаются дедупом.
    assert len(set(captured_keys)) == 5
