# -*- coding: utf-8 -*-
"""Authoritative account context and Meta page helpers for campaign creation.

Endpoints под /api (auto-discovery, prefix="/api"):
- GET /campaigns/ad-account-context?act_id={id} — durable timezone/currency evidence.
- GET /campaigns/ad-account-pages?act_id={id} — список FB-страниц (promote_pages) кабинета.

Account context читается только из ``meta_account_snapshot``.  Ни numeric offset,
ни live Graph fallback не могут авторизовать validate/launch.  Состояние
``ready`` означает валидную IANA timezone, свежую подтверждённую валюту и
поддерживаемый minor-unit exponent.

Зачем (pages): шаг «Идентичность» визарда требует page_id. Тянем доступные кабинету
страницы (`/act_{id}/promote_pages`) → байер выбирает из дропдауна вместо ручного ввода ID.

Канал: read-only `MetaApiClient.execute_graph_call` через активную Vision-сессию (как
durable timezone refresh в meta_api_worker). НЕ открываем новый браузер, НЕ дёргаем сессию
сверх одного GET.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any, Literal

import grpc
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from apps.api.deps import DepEngine
from core.browser.circuit_breaker import CircuitOpenError
from core.campaign_builder.account_context import (
    normalize_campaign_account_id,
    resolve_campaign_account_context,
)
from core.meta_api.account_tz import fetch_account_context, persist_account_context
from core.meta_api.audit import AuditedMetaApiClient
from core.meta_api.client import MetaApiClient
from core.meta_api.errors import (
    MetaApiError,
    NotFoundError,
    PermanentError,
    RateLimitedError,
    SessionUnavailableError,
    TemporaryError,
)
from core.meta_api.errors import (
    PermissionError as MetaPermissionError,
)
from core.tasks.browser_fence import (
    BrowserFenceLeaseLost,
    BrowserOperationBlocked,
    BrowserOperationFence,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/campaigns", tags=["campaigns"])

# initiated_by для audit-лога этого read-only фетча.
_INITIATED_BY = "api_campaigns_meta"


class AdAccountContextResponse(BaseModel):
    """Durable campaign account evidence; never a client-editable override."""

    account_id: str
    state: Literal["ready", "stale", "unavailable"]
    timezone_name: str | None
    currency: str | None
    currency_exponent: int | None
    observed_at: datetime | None
    next_start_date: str | None
    issue: str | None


class AdAccountPage(BaseModel):
    """FB-страница, доступная кабинету для промо (id + человекочитаемое имя)."""

    id: str
    name: str


class AdAccountPagesResponse(BaseModel):
    """Список страниц кабинета для дропдауна page_id в шаге «Идентичность»."""

    pages: list[AdAccountPage]


async def fetch_account_pages(
    client: MetaApiClient,
    numeric_act_id: str,
) -> AdAccountPagesResponse:
    """GET /act_{id}/promote_pages?fields=id,name → список страниц кабинета.

    Доменные/транспортные ошибки пробрасываются — роутер маршрутизирует их на HTTP-коды.
    Пагинацию НЕ доходим (limit=100 достаточно для UI-дропдауна). Элементы без id
    пропускаем; name пустой/None → "". id/name приводим к строке.
    """
    resp = await client.execute_graph_call(
        method="GET",
        endpoint=f"/act_{numeric_act_id}/promote_pages",
        query_params={"fields": "id,name", "limit": "100"},
        # Preserve the canonical Meta account identity so the browser agent can
        # recover or select the exact cabinet page without guessing from the URL.
        ad_account_id=f"act_{numeric_act_id}",
    )
    data = resp.get("data") or []
    pages: list[AdAccountPage] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        raw_id = item.get("id")
        if raw_id is None or str(raw_id) == "":
            continue
        pages.append(AdAccountPage(id=str(raw_id), name=str(item.get("name") or "")))
    return AdAccountPagesResponse(pages=pages)


def _build_meta_client(engine: Any) -> AuditedMetaApiClient:
    """Клиент Marketing API с auditing (как meta_api_worker._build_meta_client).

    host/port — из env BROWSER_AGENT_HOST/BROWSER_AGENT_GRPC_PORT с дефолтами под
    локальный browser-agent.
    """
    return AuditedMetaApiClient(
        engine=engine,
        initiated_by=_INITIATED_BY,
        host=os.environ.get("BROWSER_AGENT_HOST", "localhost"),
        port=int(os.environ.get("BROWSER_AGENT_GRPC_PORT", "50051")),
    )


# Причины, которые оператор увидит в визарде вместо безликого «Контекст
# недоступен». Набор фиксирован: raw exception и traceback в UI не попадают.
_REFRESH_ISSUE_MAINTENANCE = "Идёт обслуживание браузера — снимок кабинета не обновлён"
_REFRESH_ISSUE_CHANNEL = "Канал Meta недоступен — снимок кабинета не обновлён"
_REFRESH_ISSUE_RATE_LIMIT = "Meta временно ограничила запросы — снимок кабинета не обновлён"
_REFRESH_ISSUE_REJECTED = "Meta отклонила запрос по кабинету"
_REFRESH_ISSUE_EMPTY = "Meta не отдала часовой пояс и валюту по кабинету"


async def _refresh_account_context_once(engine: Any, numeric_act_id: str) -> str | None:
    """Один живой Graph-read кабинета с записью снимка в PostgreSQL.

    Возвращает None, если запись прошла, иначе — причину для оператора.
    Никогда не бросает: контракт ручки — всегда отдать состояние, а не 5xx.
    Порядок except важен: RateLimitedError — подкласс TemporaryError, а
    NotFoundError/MetaPermissionError/PermanentError — подклассы MetaApiError.
    """
    client = _build_meta_client(engine)
    try:
        async with BrowserOperationFence(
            engine,
            operation_kind="account_context_refresh",
            target=numeric_act_id,
        ) as fence:
            await client.start()
            fetched = await fetch_account_context(client, numeric_act_id)
            if fetched.timezone_name is None and fetched.currency is None:
                logger.info(
                    "ad-account-context: Meta вернула пустой контекст act_%s",
                    numeric_act_id,
                )
                return _REFRESH_ISSUE_EMPTY
            await fence.assert_held()
            await persist_account_context(
                engine,
                account_id=numeric_act_id,
                timezone_name=fetched.timezone_name,
                currency=fetched.currency,
            )
            return None
    except BrowserOperationBlocked:
        return _REFRESH_ISSUE_MAINTENANCE
    except RateLimitedError:
        logger.info("ad-account-context: Meta rate-limit act_%s", numeric_act_id)
        return _REFRESH_ISSUE_RATE_LIMIT
    except (NotFoundError, MetaPermissionError, PermanentError) as exc:
        logger.info(
            "ad-account-context: Meta отвергла act_%s error_type=%s",
            numeric_act_id,
            type(exc).__name__,
        )
        return _REFRESH_ISSUE_REJECTED
    except (
        BrowserFenceLeaseLost,
        SessionUnavailableError,
        CircuitOpenError,
        TemporaryError,
        grpc.RpcError,
    ) as exc:
        logger.warning(
            "ad-account-context: канал недоступен act_%s error_type=%s",
            numeric_act_id,
            type(exc).__name__,
        )
        return _REFRESH_ISSUE_CHANNEL
    except MetaApiError as exc:
        logger.info(
            "ad-account-context: ошибка Meta act_%s error_type=%s",
            numeric_act_id,
            type(exc).__name__,
        )
        return _REFRESH_ISSUE_REJECTED
    finally:
        try:
            await client.close()
        except Exception:  # noqa: BLE001 — закрытие канала best-effort
            pass


@router.get("/ad-account-context", response_model=AdAccountContextResponse)
async def get_ad_account_context(
    engine: DepEngine,
    act_id: str = Query(
        ...,
        description="ID рекламного кабинета (с префиксом act_ или без — нормализуется).",
    ),
) -> AdAccountContextResponse:
    """Отдать durable-состояние кабинета, подтянув снимок, если его ещё нет.

    Снимок наполняет фоновый refresh в meta_api_worker по таймеру, и до его
    первого прохода новый кабинет выглядит «Контекст недоступен» — залив
    заблокирован без объяснимой причины. Недостающее тянем прямо здесь: живое
    чтение сохраняется в PostgreSQL, а ответ формируется ПЕРЕЧИТАННОЙ строкой.
    Авторитет базы не меняется: живое значение в ответ не попадает.
    """

    try:
        context = await resolve_campaign_account_context(engine, account_id=act_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Некорректный Ad Account ID") from exc

    refresh_issue: str | None = None
    if context.state != "ready":
        refresh_issue = await _refresh_account_context_once(engine, context.account_id)
        context = await resolve_campaign_account_context(
            engine,
            account_id=context.account_id,
        )

    return AdAccountContextResponse(
        account_id=context.account_id,
        state=context.state,
        timezone_name=context.timezone_name,
        currency=context.currency,
        currency_exponent=context.currency_exponent,
        observed_at=context.observed_at,
        next_start_date=(
            context.next_start_date.isoformat() if context.next_start_date is not None else None
        ),
        # Причина про сам снимок важнее причины неудачного похода в Meta.
        issue=context.issue or (refresh_issue if context.state != "ready" else None),
    )


@router.get("/ad-account-pages", response_model=AdAccountPagesResponse)
async def get_ad_account_pages(
    engine: DepEngine,
    act_id: str = Query(
        ...,
        description="ID рекламного кабинета (с префиксом act_ или без — нормализуется).",
    ),
) -> AdAccountPagesResponse:
    """Список FB-страниц кабинета (promote_pages) для дропдауна page_id.

    400 — act_id пустой; 503 — browser-agent / Vision недоступны; 422 — Meta вернула
    ошибку или кабинет не найден. read-only: один GET /act_{id}/promote_pages через
    Vision-сессию, без открытия браузера. Массив может быть пустым (нет страниц).
    """
    try:
        numeric = normalize_campaign_account_id(act_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Некорректный Ad Account ID") from exc

    client = _build_meta_client(engine)
    try:
        async with BrowserOperationFence(
            engine,
            operation_kind="campaign_pages_read",
            target=numeric,
        ) as fence:
            await client.start()
            response = await fetch_account_pages(client, numeric)
            await fence.assert_held()
            return response
    except BrowserOperationBlocked as exc:
        raise HTTPException(
            status_code=409,
            detail="Vision maintenance is active; page lookup was not started",
        ) from exc
    except BrowserFenceLeaseLost as exc:
        raise HTTPException(
            status_code=503,
            detail="Page lookup fence was lost; retry after reconciliation",
        ) from exc
    except (SessionUnavailableError, CircuitOpenError) as exc:
        # Vision-сессия не готова / circuit OPEN — канал временно недоступен.
        logger.warning(
            "ad-account-pages: канал недоступен act_%s error_type=%s",
            numeric,
            type(exc).__name__,
        )
        raise HTTPException(status_code=503, detail="browser-agent / Vision недоступны") from exc
    except grpc.RpcError as exc:
        logger.warning(
            "ad-account-pages: gRPC ошибка act_%s error_type=%s",
            numeric,
            type(exc).__name__,
        )
        raise HTTPException(status_code=503, detail="browser-agent недоступен") from exc
    except RateLimitedError as exc:
        # Meta-side rate-limit (подкласс TemporaryError) — для одного дешёвого GET
        # ретрай не помогает; ловим РАНЬШЕ TemporaryError, отдаём 422.
        logger.info("ad-account-pages: Meta rate-limit act_%s", numeric)
        raise HTTPException(status_code=422, detail="Meta временно ограничила запросы") from exc
    except TemporaryError as exc:
        # Транзиентный сбой канала Vision (browser-agent code -2 "Failed to fetch") —
        # канал недоступен, не «кабинет битый». Честный 503.
        logger.warning("ad-account-pages: канал Vision недоступен act_%s: %s", numeric, exc)
        raise HTTPException(status_code=503, detail="browser-agent / Vision недоступны") from exc
    except (NotFoundError, MetaPermissionError, PermanentError) as exc:
        # Доменная ошибка Meta: кабинет не найден / нет прав / постоянный отказ.
        logger.info(
            "ad-account-pages: Meta отвергла act_%s error_type=%s",
            numeric,
            type(exc).__name__,
        )
        raise HTTPException(status_code=422, detail="Meta отклонила запрос") from exc
    except MetaApiError as exc:
        # Прочие доменные ошибки Meta — отдаём 422 как «не удалось получить».
        logger.info(
            "ad-account-pages: ошибка Meta act_%s error_type=%s",
            numeric,
            type(exc).__name__,
        )
        raise HTTPException(status_code=422, detail="Meta отклонила запрос") from exc
    finally:
        try:
            await client.close()
        except Exception:  # noqa: BLE001 — закрытие канала best-effort
            pass
