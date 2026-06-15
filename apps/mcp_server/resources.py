# -*- coding: utf-8 -*-
"""MCP Resources — JSON-снимки текущего состояния FB Stop Bot.

В отличие от tools (которые делают что-то), resources — read-only снимки,
которые LLM может прочитать перед формулировкой ответа. Claude Desktop
показывает их пользователю в UI с возможностью attach в контекст.

4 ресурса:
- fb-stop-bot://offers              — активные офферы (catalog.offers)
- fb-stop-bot://recent-alerts       — алерты за последние 24 часа
- fb-stop-bot://workers-health      — heartbeat'ы из Redis
- fb-stop-bot://schema-overview     — статический документ с описанием tools
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Iterable

from mcp import types
from mcp.server.lowlevel.helper_types import ReadResourceContents
from sqlalchemy import text

from core.ai_assistant.tools import GLOBAL_REGISTRY
from core.ai_assistant.tools.base import RiskLevel
from core.ai_assistant.tools.ops.get_worker_health import EXPECTED_WORKERS

if TYPE_CHECKING:  # pragma: no cover
    from apps.mcp_server.context import MCPContextManager

logger = logging.getLogger(__name__)

URI_OFFERS = "fb-stop-bot://offers"
URI_RECENT_ALERTS = "fb-stop-bot://recent-alerts"
URI_WORKERS_HEALTH = "fb-stop-bot://workers-health"
URI_SCHEMA_OVERVIEW = "fb-stop-bot://schema-overview"

# Канонический список воркеров — импорт из get_worker_health (он зеркалит
# health_watchdog и защищён контрактным тестом). Раньше тут была локальная копия
# с фантомными disable/enable (удалены) и без cabinet_scheduler/tracker_aggregator —
# ресурс врал о здоровье системы.
_EXPECTED_WORKERS: tuple[str, ...] = EXPECTED_WORKERS


def list_resources() -> list[types.Resource]:
    """Список доступных ресурсов с метаданными для UI Claude Desktop."""
    return [
        types.Resource(
            uri=URI_OFFERS,  # type: ignore[arg-type]
            name="Активные офферы",
            description="Список активных офферов из catalog.offers (code, name, vertical).",
            mimeType="application/json",
        ),
        types.Resource(
            uri=URI_RECENT_ALERTS,  # type: ignore[arg-type]
            name="Последние алерты (24ч)",
            description="WARNING/STOP события из alert_events за последние 24 часа.",
            mimeType="application/json",
        ),
        types.Resource(
            uri=URI_WORKERS_HEALTH,  # type: ignore[arg-type]
            name="Здоровье воркеров",
            description="worker:heartbeat:* из Redis — какие воркеры активны.",
            mimeType="application/json",
        ),
        types.Resource(
            uri=URI_SCHEMA_OVERVIEW,  # type: ignore[arg-type]
            name="Обзор tools",
            description="Список всех доступных tools с описанием и risk_level.",
            mimeType="text/markdown",
        ),
    ]


async def read_resource(uri: str, ctx_mgr: "MCPContextManager") -> Iterable[ReadResourceContents]:
    """Прочитать содержимое ресурса по uri.

    Возвращает Iterable[ReadResourceContents] — это формат, который MCP
    server.read_resource ожидает (см. mcp/server/lowlevel/server.py).
    """
    uri_str = str(uri)
    if uri_str == URI_OFFERS:
        body = await _read_offers(ctx_mgr)
        return [ReadResourceContents(content=body, mime_type="application/json")]
    if uri_str == URI_RECENT_ALERTS:
        body = await _read_recent_alerts(ctx_mgr)
        return [ReadResourceContents(content=body, mime_type="application/json")]
    if uri_str == URI_WORKERS_HEALTH:
        body = await _read_workers_health(ctx_mgr)
        return [ReadResourceContents(content=body, mime_type="application/json")]
    if uri_str == URI_SCHEMA_OVERVIEW:
        body = _render_schema_overview()
        return [ReadResourceContents(content=body, mime_type="text/markdown")]
    raise ValueError(f"Неизвестный resource URI: {uri_str!r}")


# ===================== implementations =====================


async def _read_offers(ctx_mgr: "MCPContextManager") -> str:
    if ctx_mgr.engine is None:
        return json.dumps({"error": "engine_unavailable"}, ensure_ascii=False)
    async with ctx_mgr.engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    """
                    SELECT code, name, vertical, is_active, ad_account_ids
                    FROM offers
                    ORDER BY code
                    """
                )
            )
        ).all()

    items = [
        {
            "code": row[0],
            "name": row[1],
            "vertical": row[2],
            "is_active": bool(row[3]),
            # Мульти-кабинет: в каких кабинетах живёт оффер. Пустой список у
            # активного оффера = оффер НЕ сканируется (стоит сказать пользователю).
            "ad_account_ids": list(row[4] or []),
        }
        for row in rows
    ]
    return json.dumps(
        {
            "uri": URI_OFFERS,
            "count": len(items),
            "items": items,
            "snapshot_at": datetime.now(timezone.utc).isoformat(),
        },
        ensure_ascii=False,
        indent=2,
    )


async def _read_recent_alerts(ctx_mgr: "MCPContextManager") -> str:
    if ctx_mgr.engine is None:
        return json.dumps({"error": "engine_unavailable"}, ensure_ascii=False)
    async with ctx_mgr.engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    """
                    SELECT ae.stage,
                           ae.state,
                           ae.matched_rule_codes,
                           ae.created_at,
                           a.fb_ad_id,
                           a.ad_name,
                           c.ad_account_id
                    FROM alert_events ae
                    JOIN fb_ads a ON a.id = ae.ad_id
                    LEFT JOIN fb_adsets s ON s.id = a.adset_id
                    LEFT JOIN fb_campaigns c ON c.id = s.campaign_id
                    WHERE ae.created_at >= NOW() - INTERVAL '24 hours'
                    ORDER BY ae.created_at DESC
                    LIMIT 50
                    """
                )
            )
        ).all()

    items: list[dict[str, Any]] = []
    for row in rows:
        stage, state, rule_codes, created_at, fb_ad_id, ad_name, ad_account_id = row
        items.append(
            {
                "stage": stage,
                "state": state,
                "rule_codes": list(rule_codes) if rule_codes else [],
                "created_at": created_at.isoformat() if created_at else None,
                "fb_ad_id": fb_ad_id,
                "ad_name": ad_name,
                # Мульти-кабинет: из какого кабинета объявление (null — legacy-каталог).
                "ad_account_id": ad_account_id,
            }
        )
    return json.dumps(
        {
            "uri": URI_RECENT_ALERTS,
            "window_hours": 24,
            "count": len(items),
            "items": items,
            "snapshot_at": datetime.now(timezone.utc).isoformat(),
        },
        ensure_ascii=False,
        indent=2,
    )


async def _read_workers_health(ctx_mgr: "MCPContextManager") -> str:
    redis_client = ctx_mgr.redis_client
    if redis_client is None:
        return json.dumps({"error": "redis_unavailable"}, ensure_ascii=False)

    items: list[dict[str, Any]] = []
    for worker in _EXPECTED_WORKERS:
        key = f"worker:heartbeat:{worker}"
        try:
            raw = await redis_client.get(key)
        except Exception as exc:
            logger.warning("redis.get(%s) failed: %s", key, exc)
            items.append({"worker": worker, "status": "redis_error", "error": str(exc)})
            continue

        if raw is None:
            items.append({"worker": worker, "status": "missing"})
            continue

        payload_str = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else str(raw)
        try:
            payload = json.loads(payload_str)
        except (ValueError, TypeError):
            payload = {"raw": payload_str[:200]}

        try:
            ttl = await redis_client.ttl(key)
        except Exception:
            ttl = None

        items.append(
            {
                "worker": worker,
                "status": "alive",
                "heartbeat": payload,
                "ttl_seconds": ttl,
            }
        )

    return json.dumps(
        {
            "uri": URI_WORKERS_HEALTH,
            "expected_workers": list(_EXPECTED_WORKERS),
            "count": len(items),
            "items": items,
            "snapshot_at": datetime.now(timezone.utc).isoformat(),
        },
        ensure_ascii=False,
        indent=2,
    )


def _render_schema_overview() -> str:
    """Markdown-инструкция для LLM-клиента (Claude Desktop / Cursor).

    Это главный «системный промпт» MCP-сервера: контекст системы, семантика
    данных, правила безопасности и рекомендованный порядок работы. Список
    tools строится динамически из GLOBAL_REGISTRY — не устаревает.
    """
    lines: list[str] = [
        "# FB Stop Bot — MCP tools",
        "",
        "MCP-сервер бота мониторинга Facebook Ads. Бот сканирует объявления "
        "(через anti-detect браузер), оценивает стоп-правила (CPA/CPM/CTR/frequency/"
        "spend-без-событий), шлёт алерты в Telegram и АВТОМАТИЧЕСКИ отключает "
        "убыточные объявления через Marketing API. Здесь крутятся реальные деньги — "
        "отвечай точно, не выдумывай данные, при сомнении читай ресурсы.",
        "",
        "## Ключевая семантика",
        "",
        "- **Оффер** — рекламируемый продукт; матчится с кампаниями по вхождению "
        "кода (напр. `DRC_CR2`) в название кампании/объявления. Гео обычно "
        "закодировано в коде оффера.",
        "- **Мульти-кабинет**: у каждого оффера список `ad_account_ids` — кабинеты, "
        "которые сканируются. Активный оффер с ПУСТЫМ списком не сканируется вовсе "
        "— это стоит подсветить пользователю как проблему.",
        "- **FSM объявления**: normal → warning_sent (80% порога) → stop_sent → "
        "disabled. `stop_sent` значит бот уже создал авто-стоп задачу.",
        "- **Деньги/латентность**: скан-цикл ~90с (чаще при угрозе); отключение "
        "исполняет meta_api_worker точно по ad_id.",
        "",
        "## Правила безопасности (ОБЯЗАТЕЛЬНО)",
        "",
        "- DRAFT-tools НЕ исполняют изменение: они кладут draft в `task_queue`, "
        "человек подтверждает в Telegram (/drafts или inline-кнопка). После вызова "
        "DRAFT-tool сообщи пользователю: «черновик создан, подтверди в Telegram».",
        "- НИКОГДА не вызывай DRAFT-tool без явной просьбы пользователя изменить "
        "рекламу. «Посмотри, что отключить» = анализ read-only tools + рекомендация, "
        "НЕ создание draft'а.",
        "- Не предлагай обойти подтверждение. Драфты протухают через 24ч (reconciler).",
        "",
        "## Рекомендованный порядок работы",
        "",
        "1. Контекст: прочитай ресурсы `offers` (коды + кабинеты) и `recent-alerts`.",
        "2. Диагностика «что-то не так» → `get_worker_health` (money-критичные: "
        "observer, meta_api, cabinet_scheduler) и `get_disable_tasks_status`.",
        "3. Метрики: `get_offer_performance` / `get_insights` (Meta, spend-сторона) "
        "+ `get_tracker_stats` (AdSet.pro, post-click: регистрации/депозиты/ROI). "
        "Расхождение Meta↔трекер — это нормально (attribution gap), упоминай его.",
        "4. Изменения — только через DRAFT-tools по явной просьбе.",
        "",
        "## Категории",
        "",
        "- **READ_ONLY** — чтение состояния (БД / Redis / Meta API), исполняются сразу.",
        "- **DRAFT_REQUIRED** — mutation (правит рекламу), создаёт draft в `task_queue`, "
        "пользователь подтверждает в Telegram inline-кнопкой.",
        "- **CREATIVE** — генерация контента через LLM (без mutations).",
        "",
        "## Доступные tools",
        "",
    ]

    for risk in (RiskLevel.READ_ONLY, RiskLevel.DRAFT_REQUIRED, RiskLevel.CREATIVE):
        handlers = sorted(GLOBAL_REGISTRY.list_by_risk(risk), key=lambda h: h.name)
        if not handlers:
            continue
        lines.append(f"### {risk.value}")
        lines.append("")
        for h in handlers:
            desc = str(h.schema.get("description") or "").strip()
            # Первый абзац — короткое описание.
            short = desc.split("\n")[0]
            lines.append(f"- **`{h.name}`** — {short}")
        lines.append("")

    lines.append("## Ресурсы")
    lines.append("")
    lines.append(f"- `{URI_OFFERS}` — активные офферы: code, vertical, кабинеты (JSON)")
    lines.append(f"- `{URI_RECENT_ALERTS}` — алерты за 24ч с кабинетом и правилами (JSON)")
    lines.append(f"- `{URI_WORKERS_HEALTH}` — heartbeat 11 воркеров (JSON)")
    lines.append(f"- `{URI_SCHEMA_OVERVIEW}` — этот документ")
    lines.append("")
    return "\n".join(lines)


__all__ = [
    "URI_OFFERS",
    "URI_RECENT_ALERTS",
    "URI_SCHEMA_OVERVIEW",
    "URI_WORKERS_HEALTH",
    "list_resources",
    "read_resource",
]
