# -*- coding: utf-8 -*-
"""Integration: MCP server list_resources / read_resource.

Покрывает:
- list_resources возвращает 4 URI (offers, recent-alerts, workers-health, schema-overview).
- read_resource(offers) — JSON со списком из БД, включает seeded offer.
- read_resource(workers-health) — JSON со status'ами из fake_redis (alive/missing).
- read_resource(schema-overview) — Markdown со списком зарегистрированных tools.
- read_resource неизвестного URI — ошибка (исключение).
"""

from __future__ import annotations

import json
import uuid

import pytest
import pytest_asyncio
from mcp import types
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from apps.mcp_server.context import MCPContextManager
from apps.mcp_server.main import build_server
from apps.mcp_server.resources import (
    URI_OFFERS,
    URI_RECENT_ALERTS,
    URI_SCHEMA_OVERVIEW,
    URI_WORKERS_HEALTH,
)


@pytest_asyncio.fixture
async def seeded_offer(pg_engine: AsyncEngine):
    offer_id = uuid.uuid4()
    suffix = uuid.uuid4().hex[:8].upper()
    code = f"MCPR_{suffix}"
    async with pg_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO offers (id, code, name, is_active) VALUES (:i, :c, :n, true)"),
            {"i": offer_id, "c": code, "n": f"Test resource {suffix}"},
        )
    yield code
    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM offers WHERE id = :i"), {"i": offer_id})


def _get_list_resources_handler(app):
    return app.request_handlers[types.ListResourcesRequest]


def _get_read_resource_handler(app):
    return app.request_handlers[types.ReadResourceRequest]


async def _list_resources(app) -> list[types.Resource]:
    handler = _get_list_resources_handler(app)
    request = types.ListResourcesRequest(method="resources/list")
    server_result = await handler(request)
    return list(server_result.root.resources)


async def _read_resource(app, uri: str) -> list:
    """Вызвать read_resource и вернуть список ResourceContents."""
    handler = _get_read_resource_handler(app)
    request = types.ReadResourceRequest(
        method="resources/read",
        params=types.ReadResourceRequestParams(uri=uri),  # type: ignore[arg-type]
    )
    server_result = await handler(request)
    return list(server_result.root.contents)


# list_resources: ровно 4 ресурса с ожидаемыми URI.
@pytest.mark.asyncio
async def test_list_resources_returns_four_uris(
    pg_engine: AsyncEngine,
    fake_redis_client,
) -> None:
    mgr = MCPContextManager()
    mgr.engine = pg_engine
    mgr.redis_client = fake_redis_client
    app = build_server(mgr)

    resources = await _list_resources(app)
    uris = {str(r.uri).rstrip("/") for r in resources}
    expected = {
        URI_OFFERS,
        URI_RECENT_ALERTS,
        URI_WORKERS_HEALTH,
        URI_SCHEMA_OVERVIEW,
    }
    assert expected.issubset(uris), f"ожидаем {expected}, получили {uris}"


# read_resource offers — JSON с items[], содержит seeded code.
@pytest.mark.asyncio
async def test_read_resource_offers_returns_seeded_code(
    pg_engine: AsyncEngine,
    fake_redis_client,
    seeded_offer: str,
) -> None:
    mgr = MCPContextManager()
    mgr.engine = pg_engine
    mgr.redis_client = fake_redis_client
    app = build_server(mgr)

    contents = await _read_resource(app, URI_OFFERS)
    assert len(contents) >= 1
    block = contents[0]
    assert block.mimeType == "application/json"
    payload = json.loads(block.text)
    assert payload["uri"] == URI_OFFERS
    codes = {item["code"] for item in payload["items"]}
    assert seeded_offer in codes


# read_resource workers-health — отличает alive от missing.
@pytest.mark.asyncio
async def test_read_resource_workers_health_alive_vs_missing(
    pg_engine: AsyncEngine,
    fake_redis_client,
) -> None:
    mgr = MCPContextManager()
    mgr.engine = pg_engine
    mgr.redis_client = fake_redis_client
    app = build_server(mgr)

    # Прокинем heartbeat для observer, остальные оставим без ключей.
    await fake_redis_client.set(
        "worker:heartbeat:observer",
        json.dumps({"ts": "2026-05-27T12:00:00Z", "iteration": 42}),
        ex=60,
    )

    contents = await _read_resource(app, URI_WORKERS_HEALTH)
    assert contents[0].mimeType == "application/json"
    payload = json.loads(contents[0].text)
    statuses = {item["worker"]: item["status"] for item in payload["items"]}
    assert statuses.get("observer") == "alive"
    # disable не писал → missing.
    assert statuses.get("disable") == "missing"


# read_resource schema-overview — Markdown содержит хоть один реально зарегистрированный tool.
@pytest.mark.asyncio
async def test_read_resource_schema_overview_lists_tools(
    pg_engine: AsyncEngine,
    fake_redis_client,
) -> None:
    mgr = MCPContextManager()
    mgr.engine = pg_engine
    mgr.redis_client = fake_redis_client
    app = build_server(mgr)

    contents = await _read_resource(app, URI_SCHEMA_OVERVIEW)
    assert contents[0].mimeType == "text/markdown"
    body = contents[0].text
    assert "FB Stop Bot" in body
    # get_active_offers — стабильный READ_ONLY tool, должен быть упомянут.
    assert "get_active_offers" in body
    # DRAFT секция должна включать хотя бы один request_*.
    assert "request_" in body


# read_resource recent-alerts — JSON-структура, count может быть 0.
@pytest.mark.asyncio
async def test_read_resource_recent_alerts_returns_json(
    pg_engine: AsyncEngine,
    fake_redis_client,
) -> None:
    mgr = MCPContextManager()
    mgr.engine = pg_engine
    mgr.redis_client = fake_redis_client
    app = build_server(mgr)

    contents = await _read_resource(app, URI_RECENT_ALERTS)
    assert contents[0].mimeType == "application/json"
    payload = json.loads(contents[0].text)
    assert payload["uri"] == URI_RECENT_ALERTS
    assert payload["window_hours"] == 24
    assert isinstance(payload["items"], list)


# Неизвестный URI: read_resource поднимает ValueError.
@pytest.mark.asyncio
async def test_read_resource_unknown_uri_raises(
    pg_engine: AsyncEngine,
    fake_redis_client,
) -> None:
    mgr = MCPContextManager()
    mgr.engine = pg_engine
    mgr.redis_client = fake_redis_client
    app = build_server(mgr)

    with pytest.raises(ValueError):
        await _read_resource(app, "fb-stop-bot://does-not-exist")
