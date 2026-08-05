# -*- coding: utf-8 -*-
"""FastAPI роутер Marketing-API хелперов для визарда создания кампаний.

Endpoints под /api (auto-discovery, prefix="/api"):
- GET /campaigns/ad-account-timezone?act_id={id} — автоподхват таймзоны кабинета.
- GET /campaigns/ad-account-pages?act_id={id} — список FB-страниц (promote_pages) кабинета.

Зачем (timezone): у рекламного кабинета TZ фиксируется при создании и неизменна. Визард
ставит campaign start_time = "{date}T00:00:00{tz_offset_str}" → старт кампании в полночь по
времени кабинета. Чтобы байер не вводил оффсет руками (деньги: ошибка сдвинет старт),
тянем `timezone_offset_hours_utc` из Graph и отдаём готовое число + строку для start_time.

Зачем (pages): шаг «Идентичность» визарда требует page_id. Тянем доступные кабинету
страницы (`/act_{id}/promote_pages`) → байер выбирает из дропдауна вместо ручного ввода ID.

Канал: read-only `MetaApiClient.execute_graph_call` через активную Vision-сессию (как
account_tz warmup в meta_api_worker). Для надежного self-heal передаём ad_account_id
и даём browser-agent определить нужную вкладку кабинета.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import grpc
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from apps.api.deps import DepEngine
from core.browser.circuit_breaker import CircuitOpenError
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

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/campaigns", tags=["campaigns"])

# initiated_by для audit-лога этого read-only фетча.
_INITIATED_BY = "api_campaigns_meta"


class AdAccountTimezoneResponse(BaseModel):
    """Таймзона рекламного кабинета для start_time кампании."""

    tz_offset_hours: int
    tz_offset_str: str  # ISO ±HH:00 — идёт в start_time = "{date}T00:00:00{tz_offset_str}"
    timezone_name: str


class AdAccountPage(BaseModel):
    """FB-страница, доступная кабинету для промо (id + человекочитаемое имя)."""

    id: str
    name: str


class AdAccountPagesResponse(BaseModel):
    """Список страниц кабинета для дропдауна page_id в шаге «Идентичность»."""

    pages: list[AdAccountPage]


def _normalize_act_id(act_id: str | None) -> str:
    """Числовой ID кабинета без префикса act_. Пусто/None → ''."""
    return (act_id or "").strip().removeprefix("act_").strip()


def _tz_offset_to_str(tz_offset_hours: int) -> str:
    """Сдвиг кабинета int (часы, может быть отрицательным) → ISO `±HH:00`.

    Зеркало apps.api.routers.v1.schemas.campaigns_create._tz_offset_to_str для
    int-ветки: знак + zero-pad 2 цифры + ":00" (напр. -7 → "-07:00", 3 → "+03:00").
    """
    sign = "-" if tz_offset_hours < 0 else "+"
    return f"{sign}{abs(int(tz_offset_hours)):02d}:00"


async def fetch_account_timezone(
    client: MetaApiClient,
    numeric_act_id: str,
) -> AdAccountTimezoneResponse:
    """GET /act_{id}?fields=timezone_name,timezone_offset_hours_utc → response.

    Доменные ошибки Meta (NotFound/Permission/Permanent/SessionUnavailable) и
    транспортные (CircuitOpen/grpc.RpcError) пробрасываются — роутер маршрутизирует
    их на HTTP-коды. timezone_offset_hours_utc — целое, МОЖЕТ быть отрицательным
    (напр. -7 для America/Hermosillo).
    """
    resp = await client.execute_graph_call(
        method="GET",
        endpoint=f"/act_{numeric_act_id}",
        query_params={"fields": "timezone_name,timezone_offset_hours_utc"},
        ad_account_id=f"act_{numeric_act_id}",
    )
    raw_offset = resp.get("timezone_offset_hours_utc")
    if raw_offset is None:
        # Кабинет существует, но Meta не вернула оффсет — обращаемся как с «не найден».
        raise NotFoundError(
            "Meta не вернула timezone_offset_hours_utc для кабинета",
            endpoint=f"/act_{numeric_act_id}",
        )
    try:
        offset_hours = int(raw_offset)
    except (TypeError, ValueError) as exc:
        raise NotFoundError(
            f"Некорректный timezone_offset_hours_utc: {raw_offset!r}",
            endpoint=f"/act_{numeric_act_id}",
        ) from exc
    return AdAccountTimezoneResponse(
        tz_offset_hours=offset_hours,
        tz_offset_str=_tz_offset_to_str(offset_hours),
        timezone_name=str(resp.get("timezone_name") or ""),
    )


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


@router.get("/ad-account-timezone", response_model=AdAccountTimezoneResponse)
async def get_ad_account_timezone(
    engine: DepEngine,
    act_id: str = Query(
        ...,
        description="ID рекламного кабинета (с префиксом act_ или без — нормализуется).",
    ),
) -> AdAccountTimezoneResponse:
    """Автоподхват таймзоны кабинета для start_time кампании.

    400 — act_id пустой; 503 — browser-agent / Vision недоступны; 422 — Meta вернула
    ошибку или кабинет не найден. read-only: один GET /act_{id} через Vision-сессию.
    """
    numeric = _normalize_act_id(act_id)
    if not numeric:
        raise HTTPException(status_code=400, detail="act_id пустой")

    client = _build_meta_client(engine)
    try:
        await client.start()
        return await fetch_account_timezone(client, numeric)
    except (SessionUnavailableError, CircuitOpenError) as exc:
        # Vision-сессия не готова / circuit OPEN — канал временно недоступен.
        logger.warning("ad-account-timezone: канал недоступен act_%s: %s", numeric, exc)
        raise HTTPException(status_code=503, detail="browser-agent / Vision недоступны") from exc
    except grpc.RpcError as exc:
        logger.warning("ad-account-timezone: gRPC ошибка act_%s: %s", numeric, exc)
        raise HTTPException(status_code=503, detail="browser-agent недоступен") from exc
    except RateLimitedError as exc:
        # Meta-side rate-limit (подкласс TemporaryError) — для одного дешёвого GET
        # ретрай не помогает; ловим РАНЬШЕ TemporaryError, отдаём 422.
        logger.info("ad-account-timezone: rate-limit Meta act_%s: %s", numeric, exc)
        raise HTTPException(status_code=422, detail=f"Meta вернула ошибку: {exc}") from exc
    except TemporaryError as exc:
        # Транзиентный сбой канала Vision (browser-agent code -2 "Failed to fetch" —
        # мёртвый сетевой канал, см. инцидент 2026-06-19) — канал недоступен, не
        # «кабинет битый». Честный 503, чтобы байер не правил TZ руками по ошибке.
        logger.warning("ad-account-timezone: канал Vision недоступен act_%s: %s", numeric, exc)
        raise HTTPException(status_code=503, detail="browser-agent / Vision недоступны") from exc
    except (NotFoundError, MetaPermissionError, PermanentError) as exc:
        # Доменная ошибка Meta: кабинет не найден / нет прав / постоянный отказ.
        logger.info("ad-account-timezone: Meta отвергла act_%s: %s", numeric, exc)
        raise HTTPException(status_code=422, detail=f"Meta вернула ошибку: {exc}") from exc
    except MetaApiError as exc:
        # Прочие доменные ошибки Meta (включая RateLimited/Temporary) — кабинет
        # читается одним дешёвым GET, не ретраим; отдаём 422 как «не удалось получить».
        logger.info("ad-account-timezone: ошибка Meta act_%s: %s", numeric, exc)
        raise HTTPException(status_code=422, detail=f"Meta вернула ошибку: {exc}") from exc
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

    400 — act_id пустой; 503 — browser-agent / Vision недоступны; 422 — Meta вернула
    ошибку или кабинет не найден. read-only: один GET /act_{id}/promote_pages через
    Vision-сессию. Массив может быть пустым (нет страниц).
    """
    numeric = _normalize_act_id(act_id)
    if not numeric:
        raise HTTPException(status_code=400, detail="act_id пустой")

    client = _build_meta_client(engine)
    try:
        await client.start()
        return await fetch_account_pages(client, numeric)
    except (SessionUnavailableError, CircuitOpenError) as exc:
        # Vision-сессия не готова / circuit OPEN — канал временно недоступен.
        logger.warning("ad-account-pages: канал недоступен act_%s: %s", numeric, exc)
        raise HTTPException(status_code=503, detail="browser-agent / Vision недоступны") from exc
    except grpc.RpcError as exc:
        logger.warning("ad-account-pages: gRPC ошибка act_%s: %s", numeric, exc)
        raise HTTPException(status_code=503, detail="browser-agent недоступен") from exc
    except RateLimitedError as exc:
        # Meta-side rate-limit (подкласс TemporaryError) — для одного дешёвого GET
        # ретрай не помогает; ловим РАНЬШЕ TemporaryError, отдаём 422.
        logger.info("ad-account-pages: rate-limit Meta act_%s: %s", numeric, exc)
        raise HTTPException(status_code=422, detail=f"Meta вернула ошибку: {exc}") from exc
    except TemporaryError as exc:
        # Транзиентный сбой канала Vision (browser-agent code -2 "Failed to fetch") —
        # канал недоступен, не «кабинет битый». Честный 503.
        logger.warning("ad-account-pages: канал Vision недоступен act_%s: %s", numeric, exc)
        raise HTTPException(status_code=503, detail="browser-agent / Vision недоступны") from exc
    except (NotFoundError, MetaPermissionError, PermanentError) as exc:
        # Доменная ошибка Meta: кабинет не найден / нет прав / постоянный отказ.
        logger.info("ad-account-pages: Meta отвергла act_%s: %s", numeric, exc)
        raise HTTPException(status_code=422, detail=f"Meta вернула ошибку: {exc}") from exc
    except MetaApiError as exc:
        # Прочие доменные ошибки Meta — отдаём 422 как «не удалось получить».
        logger.info("ad-account-pages: ошибка Meta act_%s: %s", numeric, exc)
        raise HTTPException(status_code=422, detail=f"Meta вернула ошибку: {exc}") from exc
    finally:
        try:
            await client.close()
        except Exception:  # noqa: BLE001 — закрытие канала best-effort
            pass
