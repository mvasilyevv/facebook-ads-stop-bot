# -*- coding: utf-8 -*-
"""Утилиты для записи и чтения аудит-лога вызовов Marketing API.

Best-effort: запись аудита не должна ломать основной flow —
все ошибки перехватываются и логируются как WARNING.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.models import MetaApiAuditLog

logger = logging.getLogger(__name__)

# Максимальная длина строки params_json перед обрезанием
_PARAMS_MAX_LEN = 10_000

# Коды ошибок Meta, соответствующие rate-limit (для query_rate_limit_headroom)
_RATE_LIMIT_CODES = frozenset({4, 17, 32, 613, 80004})

# Regex для извлечения act_XXXXXXXXX из endpoint
_ACT_RE = re.compile(r"/act_(\d+)")


def extract_ad_account_id_from_endpoint(endpoint: str) -> str | None:
    """Попытаться извлечь act_XXXXXXXXX из endpoint типа /act_XXX/insights.

    Возвращает None если не найден.

    Примеры:
        "/act_456983032490208/insights" → "act_456983032490208"
        "/me"                           → None
        "/me/adaccounts"                → None
        "/act_123/campaigns/456"        → "act_123"
    """
    match = _ACT_RE.search(endpoint)
    if match:
        return f"act_{match.group(1)}"
    return None


async def record_audit_log(
    db: AsyncSession,
    *,
    method: str,
    endpoint: str,
    params: dict[str, str] | None = None,
    request_body: str | None = None,
    response_status: int = 0,
    response_json: str | None = None,
    duration_ms: int = 0,
    initiated_by: str = "unknown",
    error_code: int | None = None,
    error_subcode: int | None = None,
    session_id: str | None = None,
    ad_account_id: str | None = None,
) -> int:
    """Записать одну запись в meta_api_audit_log.

    Возвращает id вставленной записи (BigInteger PK).

    Не выбрасывает исключение — если запись провалилась, логируем WARNING и возвращаем 0.
    Audit-логирование не должно ломать основной flow (best-effort).

    Args:
        db:              Активная async-сессия SQLAlchemy.
        method:          HTTP-метод ("GET", "POST", "DELETE").
        endpoint:        Путь к API без host, например "/act_X/insights".
        params:          Query-параметры запроса (для GET).
        request_body:    JSON-тело запроса (для POST/PUT) в виде строки.
        response_status: HTTP-статус ответа; 0 при сетевой ошибке.
        response_json:   Тело ответа в виде JSON-строки.
        duration_ms:     Время выполнения в миллисекундах.
        initiated_by:    Инициатор ("bot_observer", "ai_assistant", и т.д.).
        error_code:      Код ошибки Meta API.
        error_subcode:   Subcode ошибки Meta API.
        session_id:      ID browser-agent сессии.
        ad_account_id:   ID рекламного кабинета (act_XXX). Если не передан —
                         извлекается из endpoint по regex r'/act_(\\d+)/'.
    """
    try:
        # Пробуем извлечь ad_account_id из endpoint если явно не передан
        resolved_account_id = ad_account_id or extract_ad_account_id_from_endpoint(endpoint)

        # Обрезаем params если слишком большой
        params_json: dict[str, Any] | None = None
        if params is not None:
            serialized = json.dumps(params, ensure_ascii=False)
            if len(serialized) > _PARAMS_MAX_LEN:
                # Сохраняем как {_truncated: true, raw: <первые N символов>}
                params_json = {
                    "_truncated": True,
                    "raw": serialized[:_PARAMS_MAX_LEN],
                }
            else:
                params_json = params

        # Парсим request_body в dict для JSONB если передан
        request_body_json: dict[str, Any] | None = None
        if request_body is not None:
            try:
                parsed = json.loads(request_body)
                # Если распарсилось в dict — кладём как JSONB
                if isinstance(parsed, dict):
                    request_body_json = parsed
                else:
                    # Массив или примитив — оборачиваем в envelope
                    request_body_json = {"_value": parsed}
            except (json.JSONDecodeError, ValueError):
                # Не JSON — сохраняем как raw строку
                request_body_json = {"_raw": request_body}

        # Парсим response_json в dict для JSONB если передан
        response_json_obj: dict[str, Any] | None = None
        if response_json is not None:
            try:
                parsed_resp = json.loads(response_json)
                if isinstance(parsed_resp, dict):
                    response_json_obj = parsed_resp
                else:
                    response_json_obj = {"_value": parsed_resp}
            except (json.JSONDecodeError, ValueError):
                response_json_obj = {"_raw": response_json}

        row = MetaApiAuditLog(
            method=method,
            endpoint=endpoint,
            params_json=params_json,
            request_body_json=request_body_json,
            response_status=response_status,
            response_json=response_json_obj,
            duration_ms=duration_ms,
            initiated_by=initiated_by,
            error_code=error_code,
            error_subcode=error_subcode,
            session_id=session_id,
            ad_account_id=resolved_account_id,
        )
        db.add(row)
        await db.flush()
        return row.id

    except Exception as exc:
        logger.warning(
            "Не удалось записать аудит-лог Marketing API [%s %s]: %s",
            method,
            endpoint,
            exc,
        )
        return 0


async def query_recent_errors(
    db: AsyncSession,
    *,
    since: datetime,
    limit: int = 100,
) -> list[MetaApiAuditLog]:
    """Получить недавние ошибки (response_status >= 400 OR error_code IS NOT NULL).

    Использует partial index ix_meta_api_audit_log_errors.

    Args:
        db:    Активная async-сессия.
        since: Нижняя граница времени (включительно).
        limit: Максимальное число записей.

    Returns:
        Список записей MetaApiAuditLog, отсортированных по created_at DESC.
    """
    stmt = (
        select(MetaApiAuditLog)
        .where(
            MetaApiAuditLog.created_at >= since,
            (MetaApiAuditLog.response_status >= 400) | (MetaApiAuditLog.error_code.isnot(None)),
        )
        .order_by(MetaApiAuditLog.created_at.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def query_rate_limit_headroom(
    db: AsyncSession,
    *,
    ad_account_id: str | None = None,
    window_minutes: int = 5,
) -> dict[str, int]:
    """Вернуть статистику вызовов за последние N минут для расчёта headroom.

    Опционально фильтрует по ad_account_id.

    Args:
        db:             Активная async-сессия.
        ad_account_id:  Фильтр по кабинету. None — по всем.
        window_minutes: Окно наблюдения в минутах (от текущего момента назад).

    Returns:
        Словарь со статистикой:
        {
            "total_calls": int,
            "rate_limited_calls": int,   # error_code in (4, 17, 32, 613, 80004)
            "errored_calls": int,         # error_code not null and not rate limit
            "average_duration_ms": int,
        }
    """
    from datetime import UTC, timedelta

    since = datetime.now(UTC) - timedelta(minutes=window_minutes)

    stmt = select(MetaApiAuditLog).where(MetaApiAuditLog.created_at >= since)
    if ad_account_id is not None:
        stmt = stmt.where(MetaApiAuditLog.ad_account_id == ad_account_id)

    result = await db.execute(stmt)
    rows = list(result.scalars().all())

    total_calls = len(rows)
    rate_limited_calls = 0
    errored_calls = 0
    total_duration = 0

    for row in rows:
        total_duration += row.duration_ms
        if row.error_code is not None:
            if row.error_code in _RATE_LIMIT_CODES:
                rate_limited_calls += 1
            else:
                errored_calls += 1

    average_duration_ms = (total_duration // total_calls) if total_calls > 0 else 0

    return {
        "total_calls": total_calls,
        "rate_limited_calls": rate_limited_calls,
        "errored_calls": errored_calls,
        "average_duration_ms": average_duration_ms,
    }
