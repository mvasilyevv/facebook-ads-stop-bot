# -*- coding: utf-8 -*-
"""Рендер TG-сообщения и inline-клавиатуры для enable-рекомендаций.

Pure-функции без I/O — принимают данные, возвращают (text, reply_markup).
parse_mode = HTML (как в core/telegram/renderer.py для алертов).
"""

from __future__ import annotations

import html
from dataclasses import dataclass

from core.enable_reco.analyzer import RecommendationDecision

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


_LEVEL_PREFIX = {
    "ok": "✅ <b>Можно включать</b>",
    "warning": "🟡 <b>Возможно стоит включить</b>",
}


def _escape(s: str) -> str:
    return html.escape(s or "", quote=False)


def render_enable_reco_alert(inp: EnableRecoRenderInput) -> tuple[str, dict | None]:
    """Возвращает (text, reply_markup).

    reply_markup — inline_keyboard с одной кнопкой «Включить».
    """
    level = inp.decision.level or "warning"
    prefix = _LEVEL_PREFIX.get(level, "ℹ️")

    lines = [
        f"{prefix}",
        f"<b>{_escape(inp.ad_name or 'без названия')}</b>",
        f"<i>{_escape(inp.campaign_name)} / {_escape(inp.adset_name)}</i>",
    ]
    if inp.offer_code:
        lines.append(f"Offer: <code>{_escape(inp.offer_code)}</code>")
    lines.append("")
    lines.append("<b>Что выправилось:</b>")
    if inp.decision.reasons:
        for r in inp.decision.reasons:
            lines.append(f"  • {_escape(r)}")
    else:
        lines.append("  (нет деталей)")

    snapshot = inp.decision.snapshot or {}
    if snapshot:
        lines.append("")
        lines.append("<b>Сводка:</b>")
        if "metrics_count" in snapshot:
            lines.append(f"  метрик в окне: {snapshot['metrics_count']}")
        if "total_spend" in snapshot:
            lines.append(f"  spend за окно: {snapshot['total_spend']}")
        if "latest_deposits" in snapshot:
            lines.append(f"  свежие deposits: {snapshot['latest_deposits']}")

    lines.append("")
    lines.append(f"<code>fb_ad_id: {_escape(inp.fb_ad_id)}</code>")

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
