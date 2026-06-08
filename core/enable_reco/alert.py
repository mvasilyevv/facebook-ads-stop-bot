# -*- coding: utf-8 -*-
"""Рендер TG-сообщения и inline-клавиатуры для enable-рекомендаций.

Pure-функции без I/O — принимают данные, возвращают (text, reply_markup).
parse_mode = HTML, стиль «чистая карточка» (как алерты/дайджест).
"""

from __future__ import annotations

from dataclasses import dataclass

from core.enable_reco.analyzer import RecommendationDecision
from core.telegram import format as fmt

# Сокращённый префикс для коротких callback_data (TG лимит 64 байта).
ENABLE_RECO_CALLBACK_PREFIX = "ereco"


def build_enable_reco_callback(fb_ad_id: str) -> str:
    """Формат callback_data для inline-кнопки «Включить»."""
    return f"{ENABLE_RECO_CALLBACK_PREFIX}:{fb_ad_id}"


@dataclass(frozen=True)
class EnableRecoRenderInput:
    """Данные для рендеринга алерта."""

    fb_ad_id: str
    ad_name: str
    campaign_name: str
    adset_name: str
    offer_code: str | None
    decision: RecommendationDecision


# Уровень рекомендации → (эмодзи, заголовок).
_LEVEL_HEAD = {
    "ok": ("✅", "Можно включать"),
    "warning": ("🟡", "Возможно стоит включить"),
}


def _snapshot_grid(snapshot: dict) -> str:
    """Сводка метрик после disable → выровненный <pre>-блок (если есть данные)."""
    rows: list[list[tuple[str, str]]] = []
    if "metrics_count" in snapshot:
        rows.append([("Метрик в окне", str(snapshot["metrics_count"]))])
    if "total_spend" in snapshot:
        rows.append([("Расход", fmt.money(snapshot["total_spend"]))])
    if "latest_deposits" in snapshot:
        rows.append([("Свежие деп", fmt.num(snapshot["latest_deposits"]))])
    return fmt.kv_grid(rows)


def render_enable_reco_alert(inp: EnableRecoRenderInput) -> tuple[str, dict | None]:
    """Возвращает (text, reply_markup).

    reply_markup — inline_keyboard с одной кнопкой «Включить».
    """
    level = inp.decision.level or "warning"
    emoji, head = _LEVEL_HEAD.get(level, ("ℹ️", "Рекомендация"))
    title = inp.offer_code or inp.ad_name or "без названия"

    lines = [f"{emoji} {fmt.b(head)} · {fmt.b(title)}", ""]

    lines.append(fmt.b("Что выправилось"))
    if inp.decision.reasons:
        lines.extend(fmt.bullets(list(inp.decision.reasons)))
    else:
        lines.append("• (нет деталей)")

    grid = _snapshot_grid(inp.decision.snapshot or {})
    if grid:
        lines.append("")
        lines.append(grid)

    context = " / ".join(p for p in (inp.campaign_name, inp.adset_name) if p)
    if context:
        lines.append("")
        lines.append(fmt.quote(context))

    lines.append("")
    lines.append(fmt.code(f"id {inp.fb_ad_id}"))

    text = "\n".join(lines)

    reply_markup = {
        "inline_keyboard": [
            [
                {
                    "text": "▶️ Включить",
                    "callback_data": build_enable_reco_callback(inp.fb_ad_id),
                }
            ]
        ]
    }
    return text, reply_markup


__all__ = [
    "ENABLE_RECO_CALLBACK_PREFIX",
    "EnableRecoRenderInput",
    "build_enable_reco_callback",
    "render_enable_reco_alert",
]
