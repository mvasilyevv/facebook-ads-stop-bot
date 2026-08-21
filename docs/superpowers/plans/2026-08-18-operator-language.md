# Интерфейс перестаёт говорить транспортом — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Оператор нигде не читает транспортный префикс `act_`, машинный код причины и английский жаргон вместо русского текста.

**Architecture:** Две правки 18.08 — снятие `act_` в портфеле и замена голого `$` — лечили симптом на стороне рендера. Настоящий источник в трёх местах: бэкенд сам вклеивает `act_` в поля `name`/`label`, резолвер контекста кладёт в `issue` машинный код, а часть строк интерфейса писалась по-английски. План чинит источники и ставит гард, чтобы префикс не вернулся.

**Tech Stack:** Python 3 (FastAPI, SQLAlchemy async, pytest), TypeScript (React, vitest).

## Global Constraints

- `act_` — приставка адреса Meta. В полях идентичности (`id`, `account_id`, аргументы Graph) она остаётся; в полях, которые читает человек (`name`, `label`, текст сообщения), её быть не должно.
- Raw exception, traceback, UUID, bot token и секреты не попадают в operator UI, Telegram, URL, логи и breadcrumbs — действующий инвариант проекта. Машинный код причины (`campaign_account_context_stale`) в интерфейсе — нарушение того же правила.
- Контракт OpenAPI не меняется: правки трогают только значения полей, не их состав и не типы.
- Миграций нет: ни одна таблица и ни одна колонка не меняются.
- Тексты интерфейса — по-русски; английскими остаются имена типов, API-полей и технические идентификаторы.
- Никогда не запускать pytest против боевой БД (`:5433`): integration-фикстуры сносят `offers`/`offer_rules`. Только одноразовый контейнер.
- Один архитектурный слой за коммит; каждая задача заканчивается зелёными узкими тестами.

---

## Что нашлось

Разбор по коду 18.08.2026; каждая строка проверена grep'ом.

### Класс A — бэкенд вклеивает `act_` в человекочитаемые поля

| Место | Что отдаёт | Куда попадает |
|---|---|---|
| `apps/api/routers/v1/operator.py:962` | `name=f"act_{cabinet_id}"` | строка кабинета в «Портфеле» |
| `apps/api/routers/v1/operator.py:557` | `{"id": resolved, "name": f"act_{resolved}"}` | заголовок страницы кабинета |
| `core/analytics/performance.py:1104` | `accounts[value] = f"act_{value}"` | выпадающий фильтр «Кабинет» в аналитике |
| `apps/api/routers/v1/operator.py:180` | `", ".join(f"act_{value}" …)` | текст «Валюта не подтверждена для: …» |
| `apps/api/routers/v1/operator.py:644` | `", ".join(f"act_{value}" …)` | текст «Границы суток являются оценочными» |
| `apps/api/routers/v1/analytics.py:124` | `", ".join(f"act_{value}" …)` | текст про валюту в аналитике |

Не трогаем: `core/meta_api/identity.py:33`, `core/campaign_builder/config.py:83`,
`core/adset_duplicates/service.py:316,430` — там `act_` идёт в Graph или в поле
идентичности, это его законное место.

### Класс B — машинный код вместо причины

`core/campaign_builder/account_context.py` кладёт в `issue` строки
`campaign_account_context_unavailable`, `campaign_currency_exponent_unsupported`,
`campaign_account_context_stale`. Ручка `/campaigns/ad-account-context` отдаёт их
как есть (`campaigns_meta.py:285`), а визард печатает без перевода
(`WizardStep2Identity.tsx:332`, `CampaignWizard.tsx` в mini). Оператор читает
`campaign_account_context_stale`.

Оба фронта вдобавок подставляют свой код при отказе запроса:
`account_context_issue: "account_context_request_failed"`
(`WizardStep2Identity.tsx:105`, `CampaignWizard.tsx:225`).

Проверено: на эти строки не завязана ни одна ветка логики и ни один тест —
`issue` читают только показ и текст исключения.

### Класс C — английский в русском интерфейсе

| Место | Сейчас |
|---|---|
| `frontend/src/components/domain/campaigns/WizardStep7Launch.tsx:234` | `act_{account.account_id}` прямо в JSX |
| `frontend/src/components/domain/campaigns/WizardStep7Launch.tsx:236` | «Отдельный run» |
| `frontend/src/components/settings/ObserverTab.tsx:270` | «не завершённый scan» |
| `frontend/src/components/domain/campaigns/WizardStep2Identity.tsx:263` | «для фильтра owner_campaign_tag» |
| `frontend-mini/src/features/operator/OperatorMiniDashboard.tsx:660` | «timezone не подтверждён» (в web уже «часовой пояс») |
| `frontend-mini/src/features/settings/DisplaySettings.tsx:51,58,88,106` | «IANA timezone», «timezone отображения» |

---

## File Structure

| Файл | Ответственность | Изменение |
|---|---|---|
| `apps/api/routers/v1/operator.py` | Снимок оператора | 4 места: `name` и тексты без `act_` |
| `core/analytics/performance.py` | SQL и опции фильтров аналитики | метка кабинета без `act_` |
| `apps/api/routers/v1/analytics.py` | Ручки аналитики | текст про валюту без `act_` |
| `core/campaign_builder/account_context.py` | Резолв контекста кабинета | `issue` становится человеческим текстом |
| `tests/unit/test_operator_language.py` | Гард против возврата префикса | создать |
| `frontend/src/components/domain/campaigns/WizardStep7Launch.tsx` | Шаг «Запуск» | снять `act_`, убрать «run» |
| `frontend/src/components/settings/ObserverTab.tsx` | Настройки наблюдателя | убрать «scan» |
| `frontend/src/components/domain/campaigns/WizardStep2Identity.tsx` | Шаг «Идентичность» | подсказка про тег, свой код причины |
| `frontend-mini/src/features/operator/OperatorMiniDashboard.tsx` | Мини-дашборд | «часовой пояс» вместо «timezone» |
| `frontend-mini/src/features/settings/DisplaySettings.tsx` | Настройки отображения | «часовой пояс» вместо «timezone» |
| `frontend-mini/src/features/campaigns/CampaignWizard.tsx` | Визард mini | свой код причины |

---

### Task 1: Бэкенд перестаёт вклеивать `act_` в имена и тексты

**Files:**
- Modify: `apps/api/routers/v1/operator.py:180`, `:557`, `:644`, `:962`
- Modify: `core/analytics/performance.py:1104`
- Modify: `apps/api/routers/v1/analytics.py:124`
- Create: `tests/unit/test_operator_language.py`

**Interfaces:**
- Consumes: ничего из предыдущих задач.
- Produces: поля `name` и `label` кабинета содержат только числовой идентификатор. Task 4 опирается на это: фронтовый хелпер `operatorCabinetDisplayName` остаётся как защита, но перестаёт быть единственным местом снятия префикса.

- [ ] **Step 1: Написать падающий гард**

Создать `tests/unit/test_operator_language.py`:

```python
# -*- coding: utf-8 -*-
"""Оператор не читает транспортный префикс кабинета.

18.08.2026 владелец увидел в «Портфеле» строку `act_1234567890123456`. Префикс
`act_` — приставка адреса Meta: он одинаков во всех строках, удлиняет их и
мешает сверить номер глазами. В полях идентичности он законен, в тексте для
человека — нет.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Модули, чьи строки доходят до операторского интерфейса как есть.
_OPERATOR_FACING = (
    "apps/api/routers/v1/operator.py",
    "apps/api/routers/v1/analytics.py",
    "core/analytics/performance.py",
)

# Ловим склейку префикса в Python-строке: f"act_{...}" в любом виде.
_ACT_PREFIX = re.compile(r'f"act_\{')


def test_operator_facing_modules_do_not_mint_act_prefix() -> None:
    offenders: list[str] = []
    for relative in _OPERATOR_FACING:
        source = (ROOT / relative).read_text(encoding="utf-8")
        for number, line in enumerate(source.splitlines(), start=1):
            if _ACT_PREFIX.search(line):
                offenders.append(f"{relative}:{number}")
    assert offenders == [], (
        "префикс act_ вклеивается в операторский текст: "
        + ", ".join(offenders)
        + " — отдавайте числовой идентификатор, префикс нужен только Graph"
    )
```

- [ ] **Step 2: Убедиться, что гард падает**

Run:
```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/unit/test_operator_language.py -q
```
Expected: FAIL со списком шести мест — `apps/api/routers/v1/operator.py:180`, `:557`, `:644`, `:962`, `apps/api/routers/v1/analytics.py:124`, `core/analytics/performance.py:1104`.

- [ ] **Step 3: Снять префикс в снимке оператора**

В `apps/api/routers/v1/operator.py` заменить строку 180

```python
    missing = ", ".join(f"act_{value}" for value in currencies.missing_account_ids)
```

на

```python
    missing = ", ".join(currencies.missing_account_ids)
```

Заменить строку 557

```python
    return {"id": resolved, "name": f"act_{resolved}"}
```

на

```python
    # Идентичность остаётся с префиксом там, где её потребляет Graph; имя для
    # человека — только номер (18.08.2026: префикс мешал сверять кабинет).
    return {"id": resolved, "name": resolved}
```

Заменить строку 644

```python
        missing = ", ".join(f"act_{value}" for value in cabinet_days.missing_account_ids)
```

на

```python
        missing = ", ".join(cabinet_days.missing_account_ids)
```

Заменить строку 962

```python
            name=f"act_{cabinet_id}",
```

на

```python
            name=cabinet_id,
```

- [ ] **Step 4: Снять префикс в аналитике**

В `core/analytics/performance.py` заменить строку 1104

```python
            accounts[value] = f"act_{value}"
```

на

```python
            # Метка фильтра — то, что читает байер: номер без приставки адреса.
            accounts[value] = value
```

В `apps/api/routers/v1/analytics.py` заменить строку 124

```python
    missing = ", ".join(f"act_{value}" for value in currencies.missing_account_ids)
```

на

```python
    missing = ", ".join(currencies.missing_account_ids)
```

- [ ] **Step 5: Убедиться, что гард проходит**

Run:
```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/unit/test_operator_language.py -q
```
Expected: PASS.

- [ ] **Step 6: Прогнать тесты, знающие про имя кабинета**

Run:
```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/unit/test_operator_contract.py tests/unit/test_analytics_budget.py tests/unit/test_analytics_performance_nulls.py -q
```
Expected: PASS. Если тест ожидает `act_…` в `name` или в метке фильтра, поправить ожидание на номер без префикса — это и есть проверяемое изменение.

- [ ] **Step 7: Коммит**

```bash
git add apps/api/routers/v1/operator.py apps/api/routers/v1/analytics.py core/analytics/performance.py tests/unit/test_operator_language.py
git commit -m "fix(operator): префикс act_ не вклеивается в имена и тексты для оператора"
```

---

### Task 2: Причина приходит текстом, а не машинным кодом

**Files:**
- Modify: `core/campaign_builder/account_context.py:110`, `:124`, `:137`, `:53`
- Modify: `tests/unit/test_api_campaigns_meta_context.py`

**Interfaces:**
- Consumes: ничего из Task 1.
- Produces: `CampaignAccountContext.issue` — русский текст для показа оператору; `CampaignAccountContextError` сохраняет английское сообщение для логов. Task 4 опирается на то, что фронту больше не нужно переводить коды.

- [ ] **Step 1: Написать падающий тест**

В шапку `tests/unit/test_api_campaigns_meta_context.py` добавить рядом с
`from __future__ import annotations`:

```python
import inspect
```

и рядом с остальными импортами проекта:

```python
import core.campaign_builder.account_context as account_context
```

Добавить в конец файла:

```python
# 18.08.2026: визард печатал причину как есть, и оператор читал
# «campaign_account_context_stale». Машинный код в интерфейсе — то же самое
# нарушение, что и traceback: человеку он ничего не объясняет.
def test_issue_is_operator_text_not_machine_code() -> None:
    source = inspect.getsource(account_context)
    for code in (
        "campaign_account_context_unavailable",
        "campaign_currency_exponent_unsupported",
        "campaign_account_context_stale",
    ):
        assert f'issue="{code}"' not in source


# Текст причины предназначен интерфейсу, а сообщение исключения — журналу:
# в логах нужна грепаемая английская строка, а не операторская фраза.
def test_context_error_message_stays_greppable() -> None:
    context = CampaignAccountContext(
        account_id="1234567890123456",
        state="unavailable",
        timezone_name=None,
        currency=None,
        currency_exponent=None,
        observed_at=None,
        next_start_date=None,
        issue="Meta ещё не подтвердила часовой пояс и валюту кабинета",
    )
    message = str(account_context.CampaignAccountContextError(context))
    assert "campaign account context is unavailable" in message
    assert "act_1234567890123456" in message


- [ ] **Step 2: Убедиться, что тест падает**

Run:
```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/unit/test_api_campaigns_meta_context.py -q
```
Expected: FAIL — `assert 'issue="campaign_account_context_unavailable"' not in source`.

- [ ] **Step 3: Заменить коды текстом**

В `core/campaign_builder/account_context.py` добавить рядом с импортами:

```python
# Причина едет прямо в интерфейс визарда, поэтому она написана для человека.
# Машинный код (`campaign_account_context_stale`) оператору ничего не объяснял.
_ISSUE_UNAVAILABLE = "Meta ещё не подтвердила часовой пояс и валюту кабинета"
_ISSUE_EXPONENT = "Валюта кабинета не поддерживается для денежных полей"
_ISSUE_STALE = "Подтверждение кабинета устарело — нужен свежий снимок"
```

Заменить `issue="campaign_account_context_unavailable",` на `issue=_ISSUE_UNAVAILABLE,`.

Заменить `issue="campaign_currency_exponent_unsupported",` на `issue=_ISSUE_EXPONENT,`.

Заменить `issue="campaign_account_context_stale",` на `issue=_ISSUE_STALE,`.

- [ ] **Step 4: Сообщение исключения остаётся английским**

В том же файле заменить строку 53

```python
        super().__init__(context.issue or "campaign account context is unavailable")
```

на

```python
        # В логах нужна грепаемая строка, а не операторский текст: `issue`
        # переведён на русский и предназначен интерфейсу, не журналу.
        super().__init__(
            f"campaign account context is {context.state} for act_{context.account_id}"
        )
```

- [ ] **Step 5: Убедиться, что тесты проходят**

Run:
```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/unit/test_api_campaigns_meta_context.py tests/unit/test_api_campaigns_meta_pages.py -q
```
Expected: PASS. Если какой-то тест сравнивал `issue` с кодом, поправить ожидание на новый текст.

- [ ] **Step 6: Коммит**

```bash
git add core/campaign_builder/account_context.py tests/unit/test_api_campaigns_meta_context.py
git commit -m "fix(campaigns): причина недоступного контекста приходит текстом, а не кодом"
```

---

### Task 3: Оба фронта перестают показывать свой код и чужой префикс

**Files:**
- Modify: `frontend/src/components/domain/campaigns/WizardStep2Identity.tsx:105`, `:263`
- Modify: `frontend/src/components/domain/campaigns/WizardStep7Launch.tsx:234`, `:236`
- Modify: `frontend-mini/src/features/campaigns/CampaignWizard.tsx:225`
- Modify: `frontend/src/tests/components/WizardStep2Identity.test.tsx`

**Interfaces:**
- Consumes: Task 2 — бэкенд отдаёт `issue` человеческим текстом.
- Produces: ни один фронт не подставляет собственный машинный код в `account_context_issue`.

- [ ] **Step 1: Написать падающий тест**

Добавить в `frontend/src/tests/components/WizardStep2Identity.test.tsx` внутрь
блока `describe("WizardStep2Identity — контекст кабинета", …)`:

```tsx
  // Свой машинный код фронт подставлял сам при отказе запроса — оператор читал
  // «account_context_request_failed» и шёл гадать (18.08.2026).
  it("не показывает машинный код причины", () => {
    renderStep({
      act_id: "1234567890123456",
      account_context_state: "unavailable",
      account_context_issue: "account_context_request_failed",
    });

    expect(screen.queryByText(/account_context_request_failed/)).toBeNull();
  });
```

- [ ] **Step 2: Убедиться, что тест падает**

Run:
```bash
cd frontend && npx vitest run src/tests/components/WizardStep2Identity.test.tsx
```
Expected: FAIL — элемент с текстом `account_context_request_failed` найден.

- [ ] **Step 3: Убрать подстановку кода в web-визарде**

В `frontend/src/components/domain/campaigns/WizardStep2Identity.tsx` заменить

```tsx
          account_context_issue: "account_context_request_failed",
```

на

```tsx
          account_context_issue: "Не удалось запросить подтверждение у Meta",
```

- [ ] **Step 4: Убрать подстановку кода в mini-визарде**

В `frontend-mini/src/features/campaigns/CampaignWizard.tsx` заменить ту же строку

```tsx
          account_context_issue: "account_context_request_failed",
```

на

```tsx
          account_context_issue: "Не удалось запросить подтверждение у Meta",
```

- [ ] **Step 5: Убрать `act_` и «run» из шага «Запуск»**

В `frontend/src/components/domain/campaigns/WizardStep7Launch.tsx` заменить

```tsx
            <strong className="font-numeric text-[13px] text-bg-11">act_{account.account_id}</strong>
            <span className="text-[12px] text-bg-9">
              {account.run_id ? "Отдельный run" : "Не поставлен в очередь"}
            </span>
```

на

```tsx
            <strong className="font-numeric text-[13px] text-bg-11">
              {account.account_id}
            </strong>
            <span className="text-[12px] text-bg-9">
              {account.run_id ? "Отдельный запуск" : "Не поставлен в очередь"}
            </span>
```

- [ ] **Step 6: Убрать имя поля из подсказки про тег байера**

В `frontend/src/components/domain/campaigns/WizardStep2Identity.tsx` заменить

```tsx
            helpText="Опционально — для фильтра owner_campaign_tag"
```

на

```tsx
            helpText="Необязательно — по нему потом фильтруются кампании байера"
```

- [ ] **Step 7: Убедиться, что тесты проходят**

Run:
```bash
cd frontend && npx vitest run src/tests/components/WizardStep2Identity.test.tsx src/tests/pages/CampaignCreate.test.tsx
```
Expected: PASS. Если тест ожидал `act_` на шаге «Запуск», поправить ожидание на номер без префикса.

- [ ] **Step 8: Коммит**

```bash
git add frontend/src/components/domain/campaigns frontend-mini/src/features/campaigns frontend/src/tests
git commit -m "fix(campaigns): визард не показывает машинный код и чужой префикс"
```

---

### Task 4: Английские слова уходят из русских строк

**Files:**
- Modify: `frontend/src/components/settings/ObserverTab.tsx:270`
- Modify: `frontend-mini/src/features/operator/OperatorMiniDashboard.tsx:660`
- Modify: `frontend-mini/src/features/settings/DisplaySettings.tsx:51`, `:58`, `:88`, `:106`

**Interfaces:**
- Consumes: ничего из Task 1-3.
- Produces: ничего для последующих задач; правка текстовая.

- [ ] **Step 1: Убрать «scan» из настроек наблюдателя**

В `frontend/src/components/settings/ObserverTab.tsx` заменить

```tsx
"Создаёт отдельную задачу. Ответ означает только постановку в очередь, не завершённый scan."
```

на

```tsx
"Создаёт отдельную задачу. Ответ означает только постановку в очередь, а не завершённый скан."
```

- [ ] **Step 2: Синхронизировать формулировку часового пояса в mini**

В `frontend-mini/src/features/operator/OperatorMiniDashboard.tsx` заменить

```tsx
            {cabinet.timezone ?? "timezone не подтверждён"}
```

на

```tsx
            {cabinet.timezone ?? "часовой пояс не подтверждён"}
```

Тот же текст уже стоит в web-версии (`OperatorDashboard.tsx`) — фронты
перестают расходиться на одной и той же строке.

- [ ] **Step 3: Убрать «timezone» из настроек отображения**

В `frontend-mini/src/features/settings/DisplaySettings.tsx` заменить все четыре строки:

- `"Введите IANA timezone без пробелов"` → `"Введите название часового пояса IANA без пробелов"` (две строки, 51 и 58);
- `"Не удалось загрузить timezone отображения"` → `"Не удалось загрузить часовой пояс отображения"`;
- `"Не удалось сохранить timezone отображения"` → `"Не удалось сохранить часовой пояс отображения"`.

- [ ] **Step 4: Прогнать тесты обоих фронтов**

Run:
```bash
pnpm -r test
```
Expected: PASS. Если тест сравнивал строку дословно, поправить ожидание на новый текст.

- [ ] **Step 5: Коммит**

```bash
git add frontend/src/components/settings frontend-mini/src/features
git commit -m "fix(ui): русские строки интерфейса перестают говорить по-английски"
```

---

### Task 5: Полные гейты

**Files:**
- Изменений кода нет; задача — доказательства.

**Interfaces:**
- Consumes: всё, что сделано в Task 1-4.
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

Run:
```bash
docker rm -f fb-agent-test-db 2>/dev/null; docker run --rm -d --name fb-agent-test-db -e POSTGRES_PASSWORD=test -e POSTGRES_USER=test -e POSTGRES_DB=fb_agent_ci_test -p 55432:5432 postgres:16
```
Expected: печатается id контейнера.

Run:
```bash
sleep 9 && TEST_DATABASE_URL="postgresql+asyncpg://test:test@127.0.0.1:55432/fb_agent_ci_test" PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/integration -q
```
Expected: PASS.

Run:
```bash
docker rm -f fb-agent-test-db
```
Expected: печатается имя контейнера.

- [ ] **Step 3: Фронтовый workspace**

Run:
```bash
pnpm -r typecheck && pnpm -r lint && pnpm -r test && pnpm -r build
```
Expected: PASS на всех шести пакетах.

- [ ] **Step 4: Контракт не поехал**

Run:
```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/export_openapi.py && pnpm run format:openapi && pnpm gen:api && git status --porcelain frontend/openapi.json packages/shared/src/api/generated.ts
```
Expected: пустой вывод — менялись значения полей, не их состав.

- [ ] **Step 5: Проверка глазами после выката**

Открыть «Сейчас» и убедиться, что строка кабинета читается как
`1234567890123456 · USD · America/Dawson_Creek`. Открыть аналитику и проверить
выпадающий фильтр «Кабинет» — номера без `act_`.

---

## Что требуется от владельца

1. **Запустить деплой.** Push в `main` не выкатывает: нужен ручной `workflow_dispatch` на workflow `Release`.
2. **Посмотреть глазами** портфель и фильтр аналитики после выката.

## Что план сознательно не делает

- Не трогает `adset` в текстах визарда (`«Число adset'ов»`, `«ABO — бюджет на adset'ах»`). Это доменное слово байера, а не утечка транспорта: адсет — то, чем он оперирует каждый день. Апостроф там некрасив, но замена на «адсет» — вопрос вкуса, а не понятности, и её стоит решать отдельно.
- Не убирает `act_` из `id`, `account_id` и аргументов Graph: там префикс — часть адреса Meta и его снятие сломало бы вызовы.
- Не переводит `OperatorIssue.code` (`currency_unknown`, `cabinet_timezone_unknown`): это машинное поле контракта, оператору показывается `title`/`detail`, а не `code`.
- Не трогает фронтовый `operatorCabinetDisplayName`: после Task 1 он перестаёт быть единственной защитой, но остаётся дешёвой страховкой на случай старого ответа API.
