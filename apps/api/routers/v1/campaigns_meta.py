# -*- coding: utf-8 -*-
"""Authoritative account context and Meta page helpers for campaign creation.

Endpoints под /api (auto-discovery, prefix="/api"):
- GET /campaigns/ad-account-context?act_id={id} — durable timezone/currency evidence.
- GET /campaigns/ad-account-pages?act_id={id} — список FB-страниц (promote_pages) кабинета.
- GET /campaigns/ad-account-pixels?act_id={id} — список пикселей (adspixels) кабинета.

Account context читается только из ``meta_account_snapshot``.  Ни numeric offset,
ни live Graph fallback не могут авторизовать validate/launch.  Состояние
``ready`` означает валидную IANA timezone, свежую подтверждённую валюту,
поддерживаемый minor-unit exponent и подтверждённо активный статус кабинета.
Отключённый кабинет — это ``unavailable`` с причиной на языке оператора.

Зачем (справочники): шаг «Идентичность» визарда требует page_id и pixel_id. Тянем
доступные кабинету страницы (`/act_{id}/promote_pages`) и пиксели (`/act_{id}/adspixels`)
→ байер выбирает из дропдауна вместо ручного ввода ID. Пустой список — валидный ответ,
а не ошибка: остаётся ручной ввод.

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
    campaign_account_context_message,
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


class AdAccountPixelsResponse(BaseModel):
    """Список пикселей кабинета для дропдауна pixel_id в шаге «Идентичность»."""

    pixels: list[AdAccountPage]


async def _fetch_account_edge(
    client: MetaApiClient,
    numeric_act_id: str,
    edge: str,
) -> list[AdAccountPage]:
    """GET /act_{id}/{edge}?fields=id,name → справочник кабинета как [{id,name}].

    Доменные/транспортные ошибки пробрасываются — роутер маршрутизирует их на HTTP-коды.
    Пагинацию НЕ доходим (limit=100 достаточно для UI-дропдауна). Элементы без id
    пропускаем; name пустой/None → "". id/name приводим к строке.
    """
    resp = await client.execute_graph_call(
        method="GET",
        endpoint=f"/act_{numeric_act_id}/{edge}",
        query_params={"fields": "id,name", "limit": "100"},
        # Preserve the canonical Meta account identity so the browser agent can
        # recover or select the exact cabinet page without guessing from the URL.
        ad_account_id=f"act_{numeric_act_id}",
    )
    items: list[AdAccountPage] = []
    for item in resp.get("data") or []:
        if not isinstance(item, dict):
            continue
        raw_id = item.get("id")
        if raw_id is None or str(raw_id) == "":
            continue
        items.append(AdAccountPage(id=str(raw_id), name=str(item.get("name") or "")))
    return items


async def fetch_account_pages(
    client: MetaApiClient,
    numeric_act_id: str,
) -> AdAccountPagesResponse:
    """Страницы, доступные кабинету для промо."""
    return AdAccountPagesResponse(
        pages=await _fetch_account_edge(client, numeric_act_id, "promote_pages")
    )


async def fetch_account_pixels(
    client: MetaApiClient,
    numeric_act_id: str,
) -> AdAccountPixelsResponse:
    """Пиксели кабинета — кандидаты на событие оптимизации кампании."""
    return AdAccountPixelsResponse(
        pixels=await _fetch_account_edge(client, numeric_act_id, "adspixels")
    )


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
                # Статус приходит тем же запросом и обязан доехать до снимка.
                # Пропуск наблюдался вживую 20.08: гейт статуса fail-closed, а
                # подтвердить его было нечем — залив блокировался навсегда.
                # Второй писатель (observer) статус передавал, но работает
                # только при включённом сканировании, а оно выключено.
                account_status=fetched.account_status,
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
        # Наружу уходит формулировка для оператора, машинный код остаётся внутри.
        issue=(
            campaign_account_context_message(context)
            or (refresh_issue if context.state != "ready" else None)
        ),
    )


_ACT_LOOKUPS: dict[str, str] = {
    "pages": "ad-account-pages",
    "pixels": "ad-account-pixels",
}


async def _read_account_lookup(
    engine: Any,
    *,
    act_id: str,
    kind: str,
    fetch: Any,
) -> Any:
    """Один read-only справочник кабинета под операционным фенсом.

    Единый маршрут ошибок на HTTP-коды для страниц и пикселей: 503 — канал
    недоступен (Vision, circuit, gRPC, транзиентный сбой), 422 — Meta отвергла
    запрос или act_id ненормализуем, 409 — идёт обслуживание браузера.
    """
    label = _ACT_LOOKUPS[kind]
    try:
        numeric = normalize_campaign_account_id(act_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Некорректный Ad Account ID") from exc

    client = _build_meta_client(engine)
    try:
        async with BrowserOperationFence(
            engine,
            operation_kind=f"campaign_{kind}_read",
            target=numeric,
        ) as fence:
            await client.start()
            response = await fetch(client, numeric)
            await fence.assert_held()
            return response
    except BrowserOperationBlocked as exc:
        raise HTTPException(
            status_code=409,
            detail="Vision maintenance is active; lookup was not started",
        ) from exc
    except BrowserFenceLeaseLost as exc:
        raise HTTPException(
            status_code=503,
            detail="Lookup fence was lost; retry after reconciliation",
        ) from exc
    except (SessionUnavailableError, CircuitOpenError) as exc:
        # Vision-сессия не готова / circuit OPEN — канал временно недоступен.
        logger.warning(
            "%s: канал недоступен act_%s error_type=%s", label, numeric, type(exc).__name__
        )
        raise HTTPException(status_code=503, detail="browser-agent / Vision недоступны") from exc
    except grpc.RpcError as exc:
        logger.warning("%s: gRPC ошибка act_%s error_type=%s", label, numeric, type(exc).__name__)
        raise HTTPException(status_code=503, detail="browser-agent недоступен") from exc
    except RateLimitedError as exc:
        # Meta-side rate-limit (подкласс TemporaryError) — для одного дешёвого GET
        # ретрай не помогает; ловим РАНЬШЕ TemporaryError, отдаём 422.
        logger.info("%s: Meta rate-limit act_%s", label, numeric)
        raise HTTPException(status_code=422, detail="Meta временно ограничила запросы") from exc
    except TemporaryError as exc:
        # Транзиентный сбой канала Vision (browser-agent code -2 "Failed to fetch") —
        # канал недоступен, не «кабинет битый». Честный 503.
        logger.warning("%s: канал Vision недоступен act_%s: %s", label, numeric, exc)
        raise HTTPException(status_code=503, detail="browser-agent / Vision недоступны") from exc
    except (NotFoundError, MetaPermissionError, PermanentError) as exc:
        # Доменная ошибка Meta: кабинет не найден / нет прав / постоянный отказ.
        logger.info("%s: Meta отвергла act_%s error_type=%s", label, numeric, type(exc).__name__)
        raise HTTPException(status_code=422, detail="Meta отклонила запрос") from exc
    except MetaApiError as exc:
        # Прочие доменные ошибки Meta — отдаём 422 как «не удалось получить».
        logger.info("%s: ошибка Meta act_%s error_type=%s", label, numeric, type(exc).__name__)
        raise HTTPException(status_code=422, detail="Meta отклонила запрос") from exc
    finally:
        try:
            await client.close()
        except Exception:  # noqa: BLE001 — закрытие канала best-effort
            pass


@router.get("/ad-account-pages", response_model=AdAccountPagesResponse)
async def get_ad_account_pages(
    engine: DepEngine,
    act_id: str = Query(
        ...,
        description="ID рекламного кабинета (с префиксом act_ или без — нормализуется).",
    ),
) -> AdAccountPagesResponse:
    """Список FB-страниц кабинета (promote_pages) для дропдауна page_id.

    422 — act_id ненормализуем или Meta отвергла запрос; 503 — browser-agent /
    Vision недоступны. read-only: один GET через Vision-сессию, без открытия
    браузера. Массив может быть пустым (у кабинета нет страниц).
    """
    return await _read_account_lookup(
        engine, act_id=act_id, kind="pages", fetch=fetch_account_pages
    )


@router.get("/ad-account-pixels", response_model=AdAccountPixelsResponse)
async def get_ad_account_pixels(
    engine: DepEngine,
    act_id: str = Query(
        ...,
        description="ID рекламного кабинета (с префиксом act_ или без — нормализуется).",
    ),
) -> AdAccountPixelsResponse:
    """Список пикселей кабинета (adspixels) для дропдауна pixel_id.

    Пиксель задаёт событие оптимизации кампании, и ручной ввод ID означал опечатку
    ценой открута в пустоту. Коды ответов те же, что у списка страниц. Массив может
    быть пустым (у кабинета нет пикселей) — тогда остаётся ручной ввод.
    """
    return await _read_account_lookup(
        engine, act_id=act_id, kind="pixels", fetch=fetch_account_pixels
    )
