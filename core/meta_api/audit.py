# -*- coding: utf-8 -*-
"""Audit log для Marketing API: запись каждого Graph-вызова в meta_api_audit_log.

Таблица partitioned by month (RANGE created_at). Retention 30 дней (cleanup_worker).

Использование:
    from core.meta_api.audit import AuditedMetaApiClient
    client = AuditedMetaApiClient(engine=engine, initiated_by="meta_api_worker", host=...)

Если запись в audit упала — основной запрос всё равно идёт (audit best-effort).
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from core.meta_api.client import MetaApiClient
from core.meta_api.errors import MetaApiError

logger = logging.getLogger(__name__)

# Регекс для извлечения act_XXX из endpoint (/act_123/insights → "act_123").
_AD_ACCOUNT_RE = re.compile(r"/(act_\d+)\b")


def extract_ad_account_id_from_endpoint(endpoint: str) -> str | None:
    """Из endpoint вида '/act_123/insights' вытащить 'act_123'.

    Returns None если в endpoint нет act_*.
    """
    if not endpoint:
        return None
    match = _AD_ACCOUNT_RE.search(endpoint)
    return match.group(1) if match else None


async def record_audit_log(
    engine: AsyncEngine,
    *,
    endpoint: str,
    http_method: str,
    http_status: int,
    initiated_by: str,
    ad_account_id: str | None = None,
    request_payload: dict[str, Any] | None = None,
    response_payload: dict[str, Any] | None = None,
    duration_ms: int | None = None,
) -> None:
    """INSERT в meta_api_audit_log. Best-effort: не падает на ошибке записи."""
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO meta_api_audit_log
                        (endpoint, http_method, http_status, ad_account_id,
                         initiated_by, request_payload, response_payload, duration_ms)
                    VALUES
                        (:ep, :hm, :hs, :aa, :ib,
                         CAST(:rq AS JSONB), CAST(:rs AS JSONB), :dur)
                    """
                ),
                {
                    "ep": endpoint[:128],
                    "hm": http_method.upper()[:8],
                    "hs": int(http_status),
                    "aa": ad_account_id[:32] if ad_account_id else None,
                    "ib": initiated_by[:64],
                    "rq": json.dumps(request_payload) if request_payload is not None else None,
                    "rs": json.dumps(response_payload) if response_payload is not None else None,
                    "dur": int(duration_ms) if duration_ms is not None else None,
                },
            )
    except Exception:  # noqa: BLE001 — audit не должен ронять основной запрос
        logger.exception("Не удалось записать audit log для %s %s", http_method, endpoint)


async def count_recent_calls(
    engine: AsyncEngine,
    *,
    ad_account_id: str | None = None,
    window_seconds: int = 3600,
) -> int:
    """Сколько вызовов было за последнее окно (для rate-limit headroom).

    Используется метрикой и алертом: если >100/час — близко к лимиту сессии.
    """
    params: dict[str, Any] = {"sec": int(window_seconds)}
    where_aa = ""
    if ad_account_id:
        params["aa"] = ad_account_id
        where_aa = "AND ad_account_id = :aa"

    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    f"""
                    SELECT COUNT(*) FROM meta_api_audit_log
                    WHERE created_at >= NOW() - make_interval(secs => :sec)
                      {where_aa}
                    """
                ),
                params,
            )
        ).first()
    return int(row[0]) if row else 0


class AuditedMetaApiClient(MetaApiClient):
    """MetaApiClient с автоматической записью audit log на каждый Graph-вызов.

    Поведение:
    - Перед вызовом — фиксирует start_time
    - После вызова (успех ИЛИ ошибка) — пишет в meta_api_audit_log
    - При ошибке Graph — записывает с http_status=error.code и пробрасывает exception
    - Request payload содержит method/endpoint/params (тело POST НЕ пишем — может быть большим)
    """

    def __init__(
        self,
        *,
        engine: AsyncEngine,
        initiated_by: str,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._engine = engine
        self._initiated_by = initiated_by

    async def execute_graph_call(
        self,
        *,
        method: str,
        endpoint: str,
        query_params: dict[str, str] | None = None,
        body_json: str | dict[str, Any] | None = None,
        timeout_ms: int | None = None,
    ) -> dict[str, Any]:
        start = time.monotonic()
        ad_account_id = extract_ad_account_id_from_endpoint(endpoint)
        request_payload: dict[str, Any] = {
            "method": method.upper(),
            "endpoint": endpoint,
            "params": dict(query_params or {}),
            "has_body": body_json is not None,
        }

        try:
            response = await super().execute_graph_call(
                method=method,
                endpoint=endpoint,
                query_params=query_params,
                body_json=body_json,
                timeout_ms=timeout_ms,
            )
        except MetaApiError as exc:
            duration_ms = int((time.monotonic() - start) * 1000)
            await record_audit_log(
                self._engine,
                endpoint=endpoint,
                http_method=method,
                http_status=exc.code or 0,
                initiated_by=self._initiated_by,
                ad_account_id=ad_account_id,
                request_payload=request_payload,
                response_payload={
                    "error": {
                        "code": exc.code,
                        "subcode": exc.subcode,
                        "type": type(exc).__name__,
                        "message": str(exc),
                        "fbtrace_id": exc.fbtrace_id,
                    }
                },
                duration_ms=duration_ms,
            )
            raise

        duration_ms = int((time.monotonic() - start) * 1000)
        # Response payload не пишем целиком (insights могут быть огромны) — только summary.
        # M5: для Batch API (response — список sub-results {code,body}) агрегируем коды:
        # частично/полностью провальный батч раньше логировался как success-200, скрывая
        # осиротевшие объекты. Теперь http_status=200 только если ВСЕ sub-requests ОК,
        # иначе 207 (Multi-Status) + разбивка ok/failed.
        if isinstance(response, list):
            codes = [int(r.get("code", 0)) for r in response if isinstance(r, dict)]
            ok = sum(1 for c in codes if 200 <= c < 300)
            failed = len(codes) - ok
            http_status = 200 if codes and failed == 0 else 207
            response_summary = {
                "batch": True,
                "sub_total": len(codes),
                "sub_ok": ok,
                "sub_failed": failed,
            }
        else:
            http_status = 200
            response_summary = {
                "data_items": len(response.get("data") or []) if isinstance(response, dict) else 0,
                "has_paging": isinstance(response, dict) and "paging" in response,
            }
        await record_audit_log(
            self._engine,
            endpoint=endpoint,
            http_method=method,
            http_status=http_status,
            initiated_by=self._initiated_by,
            ad_account_id=ad_account_id,
            request_payload=request_payload,
            response_payload=response_summary,
            duration_ms=duration_ms,
        )
        return response
