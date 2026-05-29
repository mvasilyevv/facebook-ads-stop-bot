# -*- coding: utf-8 -*-
"""Сериализация строки alert_events (с JOIN по ad/campaign/offer) в dict.

Дублировалось дословно в dashboard.py (list_alert_events) и dashboard_stats.py
(_query_recent_alerts). Единый источник.

Строка должна содержать атрибуты: id, stage, matched_rule_codes, metrics_json,
created_at, fb_ad_id, ad_name, campaign_name, offer_code.

`triggered_by_rule_codes` всегда None — поля нет в ORM AlertEvent (v2 хранит
только matched_rule_codes), отдаём None для совместимости с frontend-shape.

datetime `created_at` возвращается объектом — FastAPI jsonable_encoder
сериализует его в ISO-8601 при отдаче (как для Pydantic-модели AlertEventOut,
так и для dict[str, Any] в DashboardBatchOut).
"""

from __future__ import annotations

from typing import Any


def alert_event_row_to_out(row: Any) -> dict[str, Any]:
    """Конвертирует строку alert_events + JOIN'ы в dict для AlertEventOut."""
    return {
        "id": str(row.id),
        "fb_ad_id": row.fb_ad_id,
        "ad_name": row.ad_name,
        "campaign_name": row.campaign_name,
        "offer_code": row.offer_code,
        "stage": row.stage,
        "matched_rule_codes": list(row.matched_rule_codes or []),
        # triggered_by_rule_codes отсутствует в ORM AlertEvent.
        "triggered_by_rule_codes": None,
        "created_at": row.created_at,
        "alert_payload": row.metrics_json if row.metrics_json else None,
    }


__all__ = ["alert_event_row_to_out"]
