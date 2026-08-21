# Контекст кабинета подтягивается по требованию — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Визард создания кампании перестаёт ждать фонового воркера: если durable-снимка кабинета нет или он протух, ручка сама делает один живой Graph-read, сохраняет его в PostgreSQL и отвечает перечитанной строкой.

**Architecture:** `GET /api/campaigns/ad-account-context` остаётся единственной ручкой и по-прежнему отдаёт состояние из `meta_account_snapshot`. Новое — при состоянии не `ready` она один раз ходит в Meta под уже существующим фенсом `account_context_refresh`, пишет результат через `persist_account_context` и **перечитывает** контекст из базы. PostgreSQL остаётся единственным авторитетом: визард получает записанную строку, а не живое значение. Неудача живого чтения не превращается в 5xx — она превращается в понятную оператору причину в поле `issue`.

**Tech Stack:** Python 3 (FastAPI, SQLAlchemy async, pytest), TypeScript + React (Vitest, Testing Library).

## Контекст: что именно сломано

Ручка `apps/api/routers/v1/campaigns_meta.py:135-160` только читает базу — в её докстринге так и записано: «without navigating or querying Meta live». Единственный, кто наполняет `meta_account_snapshot`, — фоновый refresh в `apps/meta_api_worker/main.py:2030-2041`, который срабатывает на холостом ходу по таймеру.

Для нового кабинета это гарантированная стена: оператор выбирает кабинет и видит «Контекст недоступен» с подписью «Запуск заблокирован до свежего подтверждения Meta», пока воркер не соблаговолит сходить за снимком.

Замер на проде 17.08.2026: `meta_account_snapshot` — ноль строк, при живом канале (`browser_channel_readiness = ready`) и поднятом meta_api-воркере. За десять минут воркер не записал ничего и **не сказал ни слова**: `core/meta_api/account_tz.py:540-541` молча делает `continue`, если Meta вернула и пояс, и валюту пустыми.

Причина в данных при этом есть: `CampaignAccountContext.issue` доезжает до фронта (`WizardStep2Identity.tsx:84` кладёт её в `account_context_issue`), но **не отрисовывается ни в одном фронте** — ни в `frontend` (`WizardStep2Identity.tsx:317`), ни в `frontend-mini` (`CampaignWizard.tsx:747`). Оператор видит безликую заглушку вместо причины.

## Global Constraints

- Money-путь: валюта, точность и часовой пояс питают бюджеты и время старта. Сначала инвариант и regression test, потом код.
- PostgreSQL остаётся единственным авторитетом контекста: ответ ручки формируется **перечитанной** строкой `meta_account_snapshot`, а не значением из живого ответа Meta. Живое чтение только пишет в базу.
- Ручка `ad-account-context` обязана всегда отвечать `200` с состоянием: визард рисует состояние, а не обрабатывает 5xx. Провал живого чтения становится текстом в `issue`.
- Raw exception, traceback, UUID и секреты не попадают в operator UI, Telegram, URL и логи: причины — фиксированный набор строк, а не `str(exc)`.
- `null` означает unknown, `0` — подтверждённый ноль.
- Комментарии и тексты для оператора по-русски; имена типов, полей API и ошибок — английские.
- Никогда не запускать pytest против боевой БД (`:5433`).
- Контракт OpenAPI не меняется: поле `issue` уже есть в `AdAccountContextResponse` (`campaigns_meta.py:75`). После работы всё равно свериться — `python scripts/export_openapi.py`, `pnpm run format:openapi`, `git status` должен быть пуст.
- Один живой Graph-read на выбор кабинета, без пагинации и без повторов: дальше работает durable-снимок.

---

## File Structure

| Файл | Ответственность | Изменение |
|---|---|---|
| `apps/api/routers/v1/campaigns_meta.py` | Ручки контекста и страниц кабинета | +`_refresh_account_context_once`, вызов из `get_ad_account_context` |
| `apps/meta_api_worker/main.py` | Фоновый refresh снимков | не меняется (правка в `account_tz`) |
| `core/meta_api/account_tz.py` | Fetch/persist снимка кабинета | пустой ответ Meta перестаёт быть тишиной |
| `frontend/src/components/domain/campaigns/WizardStep2Identity.tsx` | Шаг «Идентичность» веб-визарда | показывает причину вместо заглушки |
| `frontend-mini/src/features/campaigns/CampaignWizard.tsx` | Визард в mini app | показывает причину вместо заглушки |
| `tests/unit/test_api_campaigns_meta_context.py` | Контракт ручки контекста | +тесты подтягивания и причин |
| `tests/unit/test_account_timezone_refresh.py` | Контракт фонового refresh | создать: тест на видимость пустого ответа |
| `frontend/src/tests/components/WizardStep2Identity.test.tsx` | Тест шага «Идентичность» | создать: тесты отрисовки причины |
| `frontend-mini/src/tests/CampaignWizard.test.tsx` | Тест mini-визарда | +тест отрисовки причины |

---

### Task 1: Ручка подтягивает недостающий снимок сама

**Files:**
- Modify: `apps/api/routers/v1/campaigns_meta.py:135-160`
- Test: `tests/unit/test_api_campaigns_meta_context.py`

**Interfaces:**
- Consumes: существующие в этом же модуле `_build_meta_client(engine)`, `BrowserOperationFence`, `resolve_campaign_account_context(engine, account_id=...)`, набор ошибок Meta; из `core.meta_api.account_tz` — `fetch_account_context(client, account_id) -> FetchedAccountContext` (поля `timezone_name: str | None`, `currency: str | None`) и `persist_account_context(engine, *, account_id, timezone_name, currency, observed_at=None) -> bool`.
- Produces: `async def _refresh_account_context_once(engine, numeric_act_id: str) -> str | None` — `None` при успешной записи, иначе причина для оператора. Task 3 отрисовывает эту причину, приходящую в поле `issue` ответа.

- [ ] **Step 1: Разрешить фейку рефреша существовать в тестовом клиенте**

В `tests/unit/test_api_campaigns_meta_context.py` заменить хелпер `_client_for` целиком:

```python
def _client_for(
    monkeypatch: pytest.MonkeyPatch,
    context: CampaignAccountContext,
    *,
    refreshed: CampaignAccountContext | None = None,
    refresh_issue: str | None = None,
    refresh_calls: list[str] | None = None,
) -> TestClient:
    """Тестовый клиент ручки контекста.

    ``refreshed`` — что вернёт ПОВТОРНОЕ чтение после живого подтягивания.
    Без него повторное чтение отдаёт тот же контекст, что и первое.
    """
    reads: list[CampaignAccountContext] = [context]
    if refreshed is not None:
        reads.append(refreshed)

    async def _resolve(_engine, *, account_id: str) -> CampaignAccountContext:
        assert account_id in {"act_123", "123"}
        return reads.pop(0) if len(reads) > 1 else reads[0]

    async def _refresh(_engine, numeric_act_id: str) -> str | None:
        if refresh_calls is not None:
            refresh_calls.append(numeric_act_id)
        return refresh_issue

    monkeypatch.setattr(mod, "resolve_campaign_account_context", _resolve)
    monkeypatch.setattr(mod, "_refresh_account_context_once", _refresh)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_engine] = lambda: object()
    return TestClient(app, raise_server_exceptions=True)
```

- [ ] **Step 2: Написать падающие тесты**

Добавить в конец `tests/unit/test_api_campaigns_meta_context.py`:

```python
# Готовый снимок — живое чтение не нужно: лишний поход в Meta на каждый показ
# шага визарда дорог и ничего не уточняет.
def test_ready_context_never_touches_meta(monkeypatch: pytest.MonkeyPatch) -> None:
    refresh_calls: list[str] = []
    client = _client_for(
        monkeypatch,
        CampaignAccountContext(
            account_id="123",
            state="ready",
            timezone_name="America/New_York",
            currency="USD",
            currency_exponent=2,
            observed_at=datetime(2026, 8, 17, 8, 30, tzinfo=UTC),
            next_start_date=date(2026, 8, 18),
            issue=None,
        ),
        refresh_calls=refresh_calls,
    )

    resp = client.get("/api/campaigns/ad-account-context", params={"act_id": "act_123"})

    assert resp.status_code == 200
    assert resp.json()["state"] == "ready"
    assert refresh_calls == []


# Снимка нет — ручка подтягивает его сама и отвечает ПЕРЕЧИТАННОЙ строкой.
def test_missing_context_is_fetched_and_reread(monkeypatch: pytest.MonkeyPatch) -> None:
    refresh_calls: list[str] = []
    client = _client_for(
        monkeypatch,
        CampaignAccountContext(
            account_id="123",
            state="unavailable",
            timezone_name=None,
            currency=None,
            currency_exponent=None,
            observed_at=None,
            next_start_date=None,
            issue=None,
        ),
        refreshed=CampaignAccountContext(
            account_id="123",
            state="ready",
            timezone_name="America/New_York",
            currency="USD",
            currency_exponent=2,
            observed_at=datetime(2026, 8, 17, 9, 0, tzinfo=UTC),
            next_start_date=date(2026, 8, 18),
            issue=None,
        ),
        refresh_calls=refresh_calls,
    )

    resp = client.get("/api/campaigns/ad-account-context", params={"act_id": "act_123"})

    assert resp.status_code == 200
    payload = resp.json()
    assert refresh_calls == ["123"]
    assert payload["state"] == "ready"
    assert payload["timezone_name"] == "America/New_York"
    assert payload["currency"] == "USD"
    assert payload["currency_exponent"] == 2
    assert payload["issue"] is None


# Живое чтение не удалось — это не 5xx, а состояние с внятной причиной.
def test_failed_refresh_becomes_a_readable_issue(monkeypatch: pytest.MonkeyPatch) -> None:
    unavailable = CampaignAccountContext(
        account_id="123",
        state="unavailable",
        timezone_name=None,
        currency=None,
        currency_exponent=None,
        observed_at=None,
        next_start_date=None,
        issue=None,
    )
    client = _client_for(
        monkeypatch,
        unavailable,
        refreshed=unavailable,
        refresh_issue="Meta не отдала часовой пояс и валюту по кабинету",
    )

    resp = client.get("/api/campaigns/ad-account-context", params={"act_id": "act_123"})

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["state"] == "unavailable"
    assert payload["issue"] == "Meta не отдала часовой пояс и валюту по кабинету"


# Собственная причина из базы важнее причины неудачного подтягивания: она
# описывает сам снимок (например, неподдерживаемую валюту), а не поход в Meta.
def test_durable_issue_wins_over_refresh_issue(monkeypatch: pytest.MonkeyPatch) -> None:
    stale = CampaignAccountContext(
        account_id="123",
        state="stale",
        timezone_name="America/New_York",
        currency="USD",
        currency_exponent=2,
        observed_at=datetime(2026, 8, 1, 9, 0, tzinfo=UTC),
        next_start_date=None,
        issue="Подтверждение валюты устарело",
    )
    client = _client_for(
        monkeypatch,
        stale,
        refreshed=stale,
        refresh_issue="Канал Meta недоступен — снимок кабинета не обновлён",
    )

    resp = client.get("/api/campaigns/ad-account-context", params={"act_id": "act_123"})

    assert resp.json()["issue"] == "Подтверждение валюты устарело"
```

- [ ] **Step 3: Убедиться, что тесты падают**

Run:
```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/unit/test_api_campaigns_meta_context.py -q
```
Expected: FAIL — `AttributeError: <module 'apps.api.routers.v1.campaigns_meta'> has no attribute '_refresh_account_context_once'`.

- [ ] **Step 4: Добавить импорты живого чтения**

В `apps/api/routers/v1/campaigns_meta.py` добавить в блок импортов `core` (рядом с `from core.meta_api.audit import AuditedMetaApiClient`):

```python
from core.meta_api.account_tz import fetch_account_context, persist_account_context
```

- [ ] **Step 5: Реализовать одно живое подтягивание**

В `apps/api/routers/v1/campaigns_meta.py` вставить перед `@router.get("/ad-account-context", ...)`:

```python
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
```

- [ ] **Step 6: Позвать подтягивание из ручки**

В `apps/api/routers/v1/campaigns_meta.py` заменить тело `get_ad_account_context` (строки 143-160) на:

```python
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
```

- [ ] **Step 7: Убедиться, что тесты проходят**

Run:
```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/unit/test_api_campaigns_meta_context.py -q
```
Expected: PASS, все тесты файла зелёные.

- [ ] **Step 8: Проверить линт**

Run:
```bash
ruff check apps/api/routers/v1/campaigns_meta.py tests/unit/test_api_campaigns_meta_context.py
```
Expected: `All checks passed!`

- [ ] **Step 9: Коммит**

```bash
git add apps/api/routers/v1/campaigns_meta.py tests/unit/test_api_campaigns_meta_context.py
git commit -m "feat(campaigns): контекст кабинета подтягивается по требованию"
```

---

### Task 2: Пустой ответ Meta перестаёт быть тишиной

**Files:**
- Modify: `core/meta_api/account_tz.py:539-541`
- Test: `tests/unit/test_account_timezone_refresh.py`

**Interfaces:**
- Consumes: существующие в модуле `active_account_ids`, `fetch_account_context`, `persist_account_context`, `BrowserOperationFence`.
- Produces: поведение `refresh_account_timezones` — пропуск кабинета сопровождается предупреждением в логе. Сигнатура не меняется: `async def refresh_account_timezones(engine, client) -> int`.

- [ ] **Step 1: Написать падающий тест**

Файла ещё нет — создать `tests/unit/test_account_timezone_refresh.py` целиком с этим содержимым.

```python
# -*- coding: utf-8 -*-
"""Контракт фонового refresh снимков кабинета."""

from __future__ import annotations

import logging

import pytest

import core.meta_api.account_tz as account_tz


class _FakeFence:
    """Заглушка BrowserOperationFence: аренда всегда наша и не теряется."""

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self) -> "_FakeFence":
        return self

    async def __aexit__(self, *exc_info) -> bool:
        return False

    async def assert_held(self) -> None:
        return None


# Пустой ответ Meta раньше проглатывался молча: снимка нет, залив заблокирован,
# а в логе ни строки — причину было видно только глазами в интерфейсе.
@pytest.mark.asyncio
async def test_empty_meta_answer_is_logged_not_swallowed(monkeypatch, caplog) -> None:
    async def _accounts(_engine):
        return ["1234567890123456"]

    async def _fetch(_client, _account_id):
        return account_tz.FetchedAccountContext(timezone_name=None, currency=None)

    async def _persist(*_args, **_kwargs):
        raise AssertionError("пустой контекст записывать нечем")

    monkeypatch.setattr(account_tz, "active_account_ids", _accounts)
    monkeypatch.setattr(account_tz, "fetch_account_context", _fetch)
    monkeypatch.setattr(account_tz, "persist_account_context", _persist)
    monkeypatch.setattr(account_tz, "BrowserOperationFence", _FakeFence)

    with caplog.at_level(logging.WARNING, logger=account_tz.logger.name):
        updated = await account_tz.refresh_account_timezones(object(), object())

    assert updated == 0
    messages = [record.getMessage() for record in caplog.records]
    assert any("1234567890123456" in message for message in messages)
    assert any("пояс" in message or "валют" in message for message in messages)
```

- [ ] **Step 2: Убедиться, что тест падает**

Run:
```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/unit/test_account_timezone_refresh.py -q
```
Expected: FAIL — `assert any(...)` не находит ни одной записи: лог пуст, потому что код молча делает `continue`.

- [ ] **Step 3: Сделать пропуск видимым**

В `core/meta_api/account_tz.py` внутри `refresh_account_timezones` заменить

```python
                context = await fetch_account_context(client, canonical_id)
                if context.timezone_name is None and context.currency is None:
                    continue
```

на

```python
                context = await fetch_account_context(client, canonical_id)
                if context.timezone_name is None and context.currency is None:
                    # Молчаливый пропуск скрывал реальную причину: снимка нет,
                    # визард блокирует залив, а в логе ни строки (прод, 17.08.2026).
                    logger.warning(
                        "Meta не отдала пояс и валюту по кабинету act_%s — снимок не обновлён",
                        canonical_id,
                    )
                    continue
```

- [ ] **Step 4: Убедиться, что тест проходит**

Run:
```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/unit/test_account_timezone_refresh.py -q
```
Expected: PASS.

- [ ] **Step 5: Коммит**

```bash
git add core/meta_api/account_tz.py tests/unit/test_account_timezone_refresh.py
git commit -m "fix(meta-api): пустой ответ Meta по кабинету виден в логе"
```

---

### Task 3: Оба фронта показывают причину вместо заглушки

**Files:**
- Modify: `frontend/src/components/domain/campaigns/WizardStep2Identity.tsx:311-322`
- Modify: `frontend-mini/src/features/campaigns/CampaignWizard.tsx:745-750`
- Test: `frontend/src/tests/components/WizardStep2Identity.test.tsx`
- Test: `frontend-mini/src/tests/CampaignWizard.test.tsx`

**Interfaces:**
- Consumes: поле `issue` ответа `GET /api/campaigns/ad-account-context` из Task 1; во фронтах оно уже лежит в состоянии визарда как `values.account_context_issue` (`WizardStep2Identity.tsx:84`) и `identity.account_context_issue` (`CampaignWizard.tsx:153`).
- Produces: причина видна оператору; заглушка «Запуск заблокирован до свежего подтверждения Meta» остаётся только когда причины нет.

- [ ] **Step 1: Написать падающий тест веб-визарда**

Файла ещё нет — создать `frontend/src/tests/components/WizardStep2Identity.test.tsx` целиком. Компонент дёргает три хука данных (`useAdAccountContext`, `useAdAccountPages` из `@/lib/api/campaigns`, `useOffers` из `@/lib/api/offers`), поэтому оба модуля мокаются через `vi.mock` до импорта компонента — ровно как в соседнем `WizardStep7Launch.test.tsx`. Тип значений шага — `WizardIdentity` из `@/stores/campaignWizard`; дефолты не экспортируются, поэтому объявляем их в тесте.

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api/campaigns", () => ({
  useAdAccountContext: () => ({ mutate: vi.fn(), isPending: false }),
  useAdAccountPages: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock("@/lib/api/offers", () => ({
  useOffers: () => ({ data: [], isLoading: false }),
}));

import { WizardStep2Identity } from "@/components/domain/campaigns/WizardStep2Identity";
import type { WizardIdentity } from "@/stores/campaignWizard";

const BASE_IDENTITY: WizardIdentity = {
  act_id: "",
  ad_account_ids: [],
  page_id: "",
  pixel_id: "",
  account_context_state: "unavailable",
  timezone_name: "",
  currency: "",
  currency_exponent: null,
  account_context_observed_at: null,
  account_context_issue: null,
  offer_code: "",
  byer_tag: "",
};

function renderStep(overrides: Partial<WizardIdentity>) {
  return render(
    <WizardStep2Identity
      values={{ ...BASE_IDENTITY, ...overrides }}
      onChange={() => {}}
      onGoalChange={() => {}}
    />,
  );
}

describe("WizardStep2Identity — контекст кабинета", () => {
  // Оператору нужна причина, а не факт блокировки: «Контекст недоступен» без
  // объяснения отправляет его гадать, что именно не так с кабинетом.
  it("показывает причину недоступного контекста", () => {
    renderStep({
      act_id: "1234567890123456",
      account_context_state: "unavailable",
      account_context_issue: "Meta не отдала часовой пояс и валюту по кабинету",
    });

    expect(
      screen.getByText("Meta не отдала часовой пояс и валюту по кабинету"),
    ).toBeInTheDocument();
  });

  // Причины нет — остаётся честная общая формулировка, а не пустая строка.
  it("без причины оставляет общую формулировку", () => {
    renderStep({
      act_id: "1234567890123456",
      account_context_state: "unavailable",
      account_context_issue: null,
    });

    expect(
      screen.getByText("Запуск заблокирован до свежего подтверждения Meta."),
    ).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Убедиться, что тест падает**

Run:
```bash
cd frontend && pnpm vitest run src/tests/components/WizardStep2Identity.test.tsx
```
Expected: FAIL — `Unable to find an element with the text: Meta не отдала часовой пояс и валюту по кабинету`.

- [ ] **Step 3: Отрисовать причину в веб-визарде**

В `frontend/src/components/domain/campaigns/WizardStep2Identity.tsx` заменить блок строк 311-322 на:

```tsx
              ) : values.act_id.trim() ? (
                <>
                  <div className="flex items-center gap-2 font-medium text-warning">
                    <AlertTriangle aria-hidden="true" size={14} />
                    {values.account_context_state === "stale"
                      ? "Снимок устарел"
                      : "Контекст недоступен"}
                  </div>
                  <div className="mt-1 text-[12px] text-bg-9">
                    {/* Причина приходит из ответа ручки; без неё остаётся общая
                        формулировка, а не пустая строка. */}
                    {values.account_context_issue ??
                      "Запуск заблокирован до свежего подтверждения Meta."}
                  </div>
                </>
              ) : (
```

- [ ] **Step 4: Убедиться, что тест проходит**

Run:
```bash
cd frontend && pnpm vitest run src/tests/components/WizardStep2Identity.test.tsx
```
Expected: PASS, оба теста зелёные.

- [ ] **Step 5: Написать падающий тест mini-визарда**

В `frontend-mini/src/tests/CampaignWizard.test.tsx` мок `useCampaignWizardDraft` собирает identity инлайном с фиксированными значениями (строки 53-66). Чтобы менять её по тесту, завести hoisted-холдер. Добавить рядом с существующим `vi.hoisted`-блоком файла:

```tsx
const identityOverride = vi.hoisted(() => ({
  value: {} as Record<string, unknown>,
}));
```

В моке `useCampaignWizardDraft` дописать разложение холдера последним, сразу после `byer_tag: "MV",` и до закрывающей скобки объекта `identity`:

```tsx
          byer_tag: "MV",
          ...identityOverride.value,
        },
```

Сбрасывать холдер между тестами — в существующий `beforeEach` файла добавить строкой:

```tsx
    identityOverride.value = {};
```

И добавить сам тест в конец описанного блока:

```tsx
  // Тот же контракт в mini app: оператор видит причину, а не только факт.
  it("показывает причину неподтверждённого контекста", async () => {
    identityOverride.value = {
      account_context_state: "unavailable",
      account_context_issue: "Meta отклонила запрос по кабинету",
    };

    render(<CampaignWizard />);

    expect(await screen.findByText("Meta отклонила запрос по кабинету")).toBeVisible();
  });
```

- [ ] **Step 6: Убедиться, что тест падает**

Run:
```bash
cd frontend-mini && pnpm vitest run src/tests/CampaignWizard.test.tsx
```
Expected: FAIL — текст причины на экране не найден.

- [ ] **Step 7: Отрисовать причину в mini-визарде**

В `frontend-mini/src/features/campaigns/CampaignWizard.tsx` рядом со строкой, где сейчас выводится `ready ? "USD-контекст подтверждён" : "Контекст не подтверждён"`, добавить вторую строку с причиной:

```tsx
        {ready ? "USD-контекст подтверждён" : "Контекст не подтверждён"}
        {!ready && identity.account_context_issue ? (
          // Причина приходит из ответа ручки: без неё оператор гадает, что
          // именно не так с кабинетом.
          <span className="mt-1 block text-[12px] opacity-80">
            {identity.account_context_issue}
          </span>
        ) : null}
```

- [ ] **Step 8: Убедиться, что тест проходит**

Run:
```bash
cd frontend-mini && pnpm vitest run src/tests/CampaignWizard.test.tsx
```
Expected: PASS.

- [ ] **Step 9: Коммит**

```bash
git add frontend/src/components/domain/campaigns/WizardStep2Identity.tsx frontend/src/tests/components/WizardStep2Identity.test.tsx frontend-mini/src/features/campaigns/CampaignWizard.tsx frontend-mini/src/tests/CampaignWizard.test.tsx
git commit -m "feat(campaigns): визард показывает причину недоступного контекста"
```

---

### Task 4: Полные гейты

**Files:**
- Изменений кода нет; задача — доказательства.

**Interfaces:**
- Consumes: всё, что сделано в Task 1-3.
- Produces: зелёные гейты и неизменившийся контракт.

- [ ] **Step 1: Backend**

Run:
```bash
ruff check .
```
Expected: `All checks passed!`

Run:
```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/unit -q
```
Expected: PASS без новых падений.

- [ ] **Step 2: Integration на изолированной БД**

Боевую БД не трогать.

Run:
```bash
docker run --rm -d --name fb-agent-test-db -e POSTGRES_PASSWORD=test -e POSTGRES_USER=test -e POSTGRES_DB=fb_agent_ci_test -p 55432:5432 postgres:16
```
Expected: печатается id контейнера.

Run:
```bash
TEST_DATABASE_URL="postgresql+asyncpg://test:test@127.0.0.1:55432/fb_agent_ci_test" PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/integration -q
```
Expected: PASS.

Run:
```bash
docker rm -f fb-agent-test-db
```
Expected: печатается имя контейнера.

- [ ] **Step 3: Фронты**

Run:
```bash
pnpm -r typecheck && pnpm -r lint && pnpm -r test
```
Expected: 0 ошибок типов, 0 ошибок линта, все тесты зелёные.

- [ ] **Step 4: Контракт не поехал**

Run:
```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/export_openapi.py && pnpm run format:openapi && git status --porcelain frontend/openapi.json packages/shared/src/api/generated.ts
```
Expected: пустой вывод — поле `issue` в схеме уже было, контракт не менялся.

- [ ] **Step 5: Проверка на проде после выката**

Открыть визард создания кампании, выбрать кабинет оффера и убедиться, что блок «Контекст кабинета» либо становится зелёным «Подтверждено» с поясом и валютой, либо показывает конкретную причину вместо «Запуск заблокирован до свежего подтверждения Meta».

Проверить, что снимок действительно записался:

```bash
ssh root@62.60.150.133 "docker exec -i fb_agent_infra-postgres-1 psql -U fb_stop_bot -d fb_stop_bot -c 'select account_id, timezone_name, currency, currency_observed_at from meta_account_snapshot'"
```
Expected: строка по выбранному кабинету с непустыми `timezone_name` и `currency`.

---

## Что требуется от владельца

1. **Запустить деплой.** Push в `main` не выкатывает: нужен ручной `workflow_dispatch` на workflow `Release`. Всё до этой точки я довожу сам.
2. **Посмотреть глазами.** Открыть визард и подтвердить, что контекст подтягивается для кабинета оффера, а не остаётся заглушкой.
3. **Решение, если Meta по этим кабинетам молчит.** Если после подтягивания причина стабильно «Meta не отдала часовой пояс и валюту по кабинету», значит вопрос не в коде, а в самих кабинетах или правах токена — тогда это твоя сторона, и я дальше не полезу без команды.

## Что план сознательно не делает

- Не добавляет кнопку «обновить контекст» — подтягивание происходит само при выборе кабинета, отдельная кнопка была бы вторым способом сделать то же самое.
- Не переносит фоновый refresh из meta_api-воркера: он остаётся как страховка для кабинетов, которые оператор в визарде не открывает.
- Не трогает `GET /api/campaigns/ad-account-pages`: он уже ходит в Meta живьём и отдаёт 503 по своему контракту.
- Не меняет правило блокировки залива: без подтверждённого снимка кампания по-прежнему не запускается. Меняется только то, что снимок появляется сразу и что причина его отсутствия видна.
