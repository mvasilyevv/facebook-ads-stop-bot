# -*- coding: utf-8 -*-
"""Регрессия HIGH #14: request_bulk_pause matching по offer_code — word-boundary.

Раньше: ILIKE %code% → `offer_code='CR'` ловил `ACRO Sales`. Теперь — Postgres
regex `~*` с anchored alternation `(^|[^a-z0-9])CODE([^a-z0-9]|$)`, тут же
re.escape для спецсимволов кода.

Тесты гоняем на реальной БД из docker-compose (POSIX regex Postgres'а).
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text

from core.ai_assistant.tools.drafts.request_bulk_pause import RequestBulkPauseTool


@pytest_asyncio.fixture
async def world_with_campaigns(pg_engine):
    """Создаёт 4 кампании с разными совпадениями кода.

    Возвращает map fb_ad_id → описание для удобства проверки.
    """
    rows = [
        ("CR2_DRC", "CR2 | DRC | MV | 27.05", "AD1_CR2_DRC"),  # word-boundary CR/CR2 ok
        ("ACRO_SALES", "ACRO Sales Campaign", "AD2_ACRO"),  # CR — должно НЕ матчиться
        ("DRC_FULL", "DRC_CR2 | clean", "AD3_DRC"),  # DRC_CR2 точное вхождение
        ("CRA_DASH", "CR-A | dash | x", "AD4_CR_DASH"),  # CR-A — со спецсимволом
    ]
    suffix = uuid.uuid4().hex[:8]
    offer_id = uuid.uuid4()
    created: dict[str, str] = {}

    async with pg_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO offers (id, code, name) VALUES (:i, :c, :n)"),
            {"i": offer_id, "c": f"WB_TST_{suffix}", "n": "WB test offer"},
        )
        for short, cname, adname in rows:
            cmp_id = uuid.uuid4()
            adset_id = uuid.uuid4()
            ad_id = uuid.uuid4()
            fb_ad_id = f"99{uuid.uuid4().hex[:10]}"
            await conn.execute(
                text("INSERT INTO fb_campaigns (id, campaign_name, offer_id) VALUES (:i, :n, :o)"),
                {"i": cmp_id, "n": f"{cname} :: {suffix}", "o": offer_id},
            )
            await conn.execute(
                text("INSERT INTO fb_adsets (id, campaign_id, adset_name) VALUES (:i, :c, :n)"),
                {"i": adset_id, "c": cmp_id, "n": f"ADS_{short}_{suffix}"},
            )
            await conn.execute(
                text(
                    "INSERT INTO fb_ads (id, adset_id, fb_ad_id, ad_name, is_active) "
                    "VALUES (:i, :a, :f, :n, TRUE)"
                ),
                {"i": ad_id, "a": adset_id, "f": fb_ad_id, "n": f"{adname}_{suffix}"},
            )
            created[short] = fb_ad_id

    yield created

    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM offers WHERE id = :i"), {"i": offer_id})


# 'CR' НЕ должен ловить 'ACRO Sales Campaign' (substring-ложное совпадение).
@pytest.mark.asyncio
async def test_code_cr_does_not_match_acro(pg_engine, world_with_campaigns) -> None:
    result = await RequestBulkPauseTool._resolve_by_offer(pg_engine, "CR")
    # CR2 содержит букву R сразу после C — но это другая граница, проверим:
    # CR-A и CR2 содержат CR как префикс с word-boundary после? Нет —
    # после 'CR' идут '2' и '-', оба не word-boundary конец. Так что CR должен
    # ловить ТОЛЬКО строки где CR окружён НЕ [a-z0-9].
    # ACRO — там CR в середине слова "ACRO", и слева/справа [a-z], так что match нет.
    assert world_with_campaigns["ACRO_SALES"] not in result


# 'CR2' матчится с 'CR2 | DRC | ...' и с 'DRC_CR2 | clean'.
@pytest.mark.asyncio
async def test_code_cr2_matches_word_boundary(pg_engine, world_with_campaigns) -> None:
    result = await RequestBulkPauseTool._resolve_by_offer(pg_engine, "CR2")
    assert world_with_campaigns["CR2_DRC"] in result
    assert world_with_campaigns["DRC_FULL"] in result
    # ACRO не должно появиться
    assert world_with_campaigns["ACRO_SALES"] not in result


# Точный код 'DRC_CR2' матчит только 'DRC_CR2 | clean'.
@pytest.mark.asyncio
async def test_full_code_drc_cr2_matches_exact(pg_engine, world_with_campaigns) -> None:
    result = await RequestBulkPauseTool._resolve_by_offer(pg_engine, "DRC_CR2")
    assert world_with_campaigns["DRC_FULL"] in result
    # 'CR2 | DRC' содержит CR2 и DRC порознь — но не как одно слово DRC_CR2.
    assert world_with_campaigns["CR2_DRC"] not in result


# Спецсимволы в коде ('CR-A') не ломают regex благодаря re.escape.
@pytest.mark.asyncio
async def test_special_chars_in_offer_code_safe(pg_engine, world_with_campaigns) -> None:
    # '-' — в re.escape экранируется как `\-`, в Postgres regex это OK.
    result = await RequestBulkPauseTool._resolve_by_offer(pg_engine, "CR-A")
    assert world_with_campaigns["CRA_DASH"] in result


# Незакрытая regex-группа в коде ('(' без ')') не должна валить запрос.
@pytest.mark.asyncio
async def test_regex_metachars_escaped(pg_engine, world_with_campaigns) -> None:
    # Не должно бросить ProgrammingError — re.escape экранирует все meta.
    # Совпадений нет — просто проверяем, что запрос отрабатывает чисто.
    result = await RequestBulkPauseTool._resolve_by_offer(pg_engine, "CR2(")
    assert result == []


# Несуществующий код возвращает пустой список (а не error).
@pytest.mark.asyncio
async def test_no_matches_returns_empty(pg_engine, world_with_campaigns) -> None:
    result = await RequestBulkPauseTool._resolve_by_offer(pg_engine, "ZZZ_NONEXISTENT")
    assert result == []
