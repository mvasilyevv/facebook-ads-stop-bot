# Один источник «наших кабинетов» — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Набор «наших кабинетов» определяется ровно в одном месте — из конфигурации офферов, — и никакой модуль больше не выводит его из следов прошлых сканов.

**Architecture:** В системе сегодня сосуществуют два ответа на вопрос «какие кабинеты наши»: конфигурация (`offer_ad_accounts` активных офферов) и следы сканов (`fb_campaigns.ad_account_id`). Первый — намерение оператора, второй — производная от работы сканера. `core/meta_api/account_tz.py` единственный взял второй, и это замкнуло круг для нового кабинета. План убирает второй ответ, оставляет один, и закрепляет это структурным гардом, чтобы дубль не завёлся снова.

**Tech Stack:** Python 3 (SQLAlchemy async, FastAPI, pytest).

---

## Как это устроено сейчас

Разбор проведён по коду 18.08.2026; каждое утверждение проверено grep'ом или замером на проде.

### Четыре разных множества кабинетов

| Множество | Что это | Где определяется |
|---|---|---|
| **Конфигурация** | кабинеты активных офферов | `AdAccountCatalog.resolve_scan_set` (`core/ad_account_catalog.py:102`), обёртка `resolve_scan_account_ids` (`core/observer/accounts.py:46`) |
| **Следы сканов** | кабинеты, встреченные сканером | `fb_campaigns.ad_account_id` |
| **Явный скоуп** | что попросил вызывающий | параметр `account_ids=[...]` |
| **Identity одного объявления** | кабинет конкретного `fb_ad_id` | `load_ad_account_id_for_fb_ad` (`core/observer/accounts.py:83`) |

Четвёртое — не скоуп, а идентичность одной строки, и она законно читает `fb_ads → fb_adsets → fb_campaigns`. Гард из Task 2 обязан её не задеть.

### Кто чем пользуется

Конфигурацию берут observer (`apps/observer_worker/main.py:764`), readiness-пробы (`apps/api/routers/health.py:150`), операторский снимок (`apps/api/routers/v1/operator.py:911,1042`), гейт включения скана (`apps/api/routers/v1/settings_observer.py:388`) и проба готовности канала (`core/meta_api/browser_readiness.py`).

Следы сканов берёт **ровно один** модуль — `core/meta_api/account_tz.py:506`:

```python
async def active_account_ids(engine: AsyncEngine) -> list[str]:
    """Return explicit active cabinet identities from the campaign catalog."""
    ...
    "SELECT DISTINCT ad_account_id FROM fb_campaigns "
    "WHERE ad_account_id IS NOT NULL AND is_active = true "
```

Проверено: вне `account_tz.py` ни один модуль в `core/` и `apps/` не выводит скоуп кабинетов из `fb_campaigns`, `fb_adsets`, `fb_ads` или `ad_metrics`.

### Чем это оборачивается

`active_account_ids` — дефолтный скоуп сразу для трёх функций модуля:

- `resolve_cabinet_days` (`account_tz.py:208`) — границы суток кабинета;
- `resolve_account_currencies` (`account_tz.py:296`) — подтверждённые валюты;
- `refresh_account_timezones` (`account_tz.py:524`) — фоновое обновление снимков.

Для нового кабинета следы сканов пусты, поэтому получается замкнутый круг: визард создания кампании требует подтверждённый контекст кабинета → контекст пишется только фоновым refresh → refresh обходит только кабинеты с уже отсканированными кампаниями → у нового кабинета их нет и взяться неоткуда. При выключенном сканировании круг не размыкается никогда.

### Замер на проде 17.08.2026

```
fb_campaigns:            0 строк (сканирование выключено)
meta_account_snapshot:   0 строк
живой Graph по кабинетам оффера:
  2108857220005012: timezone='America/Dawson_Creek' currency='USD'
  3570379159805007: timezone='America/Dawson_Creek' currency='USD'
```

Данные у Meta есть. Не было пути, по которому они попадали бы в базу.

### Почему дубль вообще завёлся

Каноническая функция называется `resolve_scan_account_ids` и лежит в `core/observer/`. Для автора модуля `core/meta_api/` это выглядит как «внутреннее дело observer'а», и проще написать свой запрос, чем импортировать чужой. Имя врёт о назначении: множество используется health-проверками, операторским API, созданием кампаний и пробой канала — сканирование тут ни при чём. Task 3 чинит именно это; Task 1 и Task 2 не зависят от него.

---

## Global Constraints

- Money-путь: валюта и границы суток кабинета питают бюджеты, отчёты и время старта. Сначала инвариант и regression test, потом код.
- PostgreSQL — единственный авторитет: `meta_account_snapshot` остаётся местом хранения подтверждённых пояса и валюты.
- `null` означает unknown, `0` — подтверждённый ноль; пропуск снимка не превращается в UTC-догадку для money-путей.
- Raw exception, traceback, UUID и секреты не попадают в operator UI, Telegram, URL и логи.
- Комментарии и названия тестов по-русски там, где это помогает оператору; имена функций, полей и таблиц — английские.
- Никогда не запускать pytest против боевой БД (`:5433`): integration-фикстуры сносят `offers`/`offer_rules`. Только одноразовый контейнер.
- Один архитектурный слой за коммит; каждая задача заканчивается зелёными узкими тестами.
- Контракт OpenAPI не меняется: правки не трогают ни схемы, ни докстринги ручек. После работы всё равно свериться.
- Миграций нет: ни одна таблица и ни одна колонка не меняются.

---

## File Structure

| Файл | Ответственность | Изменение |
|---|---|---|
| `core/meta_api/account_tz.py` | Снимки кабинета и границы суток | `active_account_ids` удаляется; три вызова берут конфигурацию |
| `core/ad_account_catalog.py` | SQL каталога кабинетов и членства в офферах | Task 3: метод переименован в `resolve_configured_set` |
| `core/observer/accounts.py` | Observer-специфика поверх каталога | Task 3: `resolve_scan_account_ids` → `resolve_configured_ad_account_ids` |
| `tests/unit/test_account_scope_single_source.py` | Гард против повторения | создать |
| `tests/unit/test_account_timezone_refresh.py` | Контракт фонового refresh | +тест источника кабинетов |
| `tests/integration/test_account_context_without_scan.py` | Regression замкнутого круга | создать |
| `tests/unit/test_health_watchdog.py` | Тесты watchdog | 4 патча переименованы |
| `tests/integration/test_browser_operation_fence.py` | Тесты фенса | 2 патча переименованы |

---

### Task 1: Дубля больше нет — кабинеты берутся из конфигурации

**Files:**
- Modify: `core/meta_api/account_tz.py:208`, `:296`, `:506-518`, `:524`, `__all__`
- Modify: `tests/unit/test_health_watchdog.py` (4 вхождения строки)
- Modify: `tests/integration/test_browser_operation_fence.py` (2 вхождения строки)
- Modify: `tests/unit/test_account_timezone_refresh.py`
- Create: `tests/integration/test_account_context_without_scan.py`

**Interfaces:**
- Consumes: `resolve_scan_account_ids(engine: AsyncEngine) -> list[str]` из `core.observer.accounts` — отсортированный DISTINCT-союз кабинетов активных офферов.
- Produces: `core.meta_api.account_tz.active_account_ids` **перестаёт существовать**. Все, кто его патчил или звал, переходят на `resolve_scan_account_ids`. Task 2 опирается на то, что в `account_tz.py` не осталось имён таблиц сканов.

- [ ] **Step 1: Написать падающий unit-тест источника**

Добавить в конец `tests/unit/test_account_timezone_refresh.py`. Импорт `inspect` добавить в шапку файла рядом с `import logging`.

```python
# Кабинеты берутся из конфигурации офферов, а не из следов сканов. Каталог
# отсканированных кампаний пуст, пока выключено сканирование, и новый кабинет
# не мог получить снимок НИКОГДА: визард требует контекст, контекст обновлялся
# только для кабинетов с уже отсканированными кампаниями, а их неоткуда взять
# (прод, 17.08.2026 — таблица снимков пуста при живом канале и живых данных).
@pytest.mark.asyncio
async def test_refresh_scope_comes_from_offers(monkeypatch) -> None:
    seen: list[str] = []

    async def _configured(_engine):
        return ["2108857220005012", "3570379159805007"]

    async def _fetch(_client, account_id):
        seen.append(account_id)
        return account_tz.FetchedAccountContext(timezone_name="America/Dawson_Creek", currency="USD")

    async def _persist(_engine, *, account_id, timezone_name, currency):
        return True

    monkeypatch.setattr(account_tz, "resolve_scan_account_ids", _configured)
    monkeypatch.setattr(account_tz, "fetch_account_context", _fetch)
    monkeypatch.setattr(account_tz, "persist_account_context", _persist)
    monkeypatch.setattr(account_tz, "BrowserOperationFence", _FakeFence)

    updated = await account_tz.refresh_account_timezones(object(), object())

    assert updated == 2
    assert seen == ["2108857220005012", "3570379159805007"]


def test_account_tz_never_derives_scope_from_scan_results() -> None:
    """В модуле не должно остаться ни одного имени таблицы сканов."""
    source = inspect.getsource(account_tz)
    for table in ("fb_campaigns", "fb_adsets", "ad_metrics"):
        assert table not in source


def test_scan_derived_scope_helper_is_gone() -> None:
    assert not hasattr(account_tz, "active_account_ids")
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run:
```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/unit/test_account_timezone_refresh.py -q
```
Expected: FAIL — `AttributeError: module 'core.meta_api.account_tz' has no attribute 'resolve_scan_account_ids'`, `assert 'fb_campaigns' not in source`, `assert not hasattr(...)`.

- [ ] **Step 3: Удалить дублирующий резолвер**

В `core/meta_api/account_tz.py` добавить импорт (ruff сам поставит его в алфавитном порядке — после правки прогнать `ruff check --fix`):

```python
from core.observer.accounts import resolve_scan_account_ids
```

Удалить функцию `active_account_ids` целиком (строки 506-518) — блок

```python
async def active_account_ids(engine: AsyncEngine) -> list[str]:
    """Return explicit active cabinet identities from the campaign catalog."""
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT DISTINCT ad_account_id FROM fb_campaigns "
                    "WHERE ad_account_id IS NOT NULL AND is_active = true "
                    "ORDER BY ad_account_id"
                )
            )
        ).fetchall()
    return [str(row[0]) for row in rows if row[0]]
```

удаляется без замены.

Удалить строку `"active_account_ids",` из `__all__`.

- [ ] **Step 4: Перевести три вызова на конфигурацию**

В `resolve_cabinet_days` и в `resolve_account_currencies` заменить обе одинаковые строки

```python
    requested = await active_account_ids(engine) if account_ids is None else list(account_ids)
```

на

```python
    # Дефолтный скоуп — наши кабинеты по конфигурации офферов. Следы прошлых
    # сканов скоуп не задают: у нового кабинета их нет, и он выпадал целиком.
    requested = (
        await resolve_scan_account_ids(engine) if account_ids is None else list(account_ids)
    )
```

В `refresh_account_timezones` заменить

```python
        account_ids = await active_account_ids(engine)
```

на

```python
        account_ids = await resolve_scan_account_ids(engine)
```

- [ ] **Step 5: Проверить отсутствие циклического импорта**

Run:
```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -c "import core.meta_api.account_tz"
```
Expected: без вывода и без `ImportError`. (`core.observer.accounts` тянет `core.ad_account_catalog` и `core.meta_api.identity`; ни один из них не импортирует `account_tz` — цикла быть не должно.)

- [ ] **Step 6: Переименовать патчи в существующих тестах**

Патчи ссылаются на удалённое имя и упадут с `AttributeError`. Заменить строку `"active_account_ids"` на `"resolve_scan_account_ids"`:

Run:
```bash
python3 - <<'PY'
import pathlib
total = 0
for path in (
    "tests/unit/test_health_watchdog.py",
    "tests/integration/test_browser_operation_fence.py",
    "tests/unit/test_account_timezone_refresh.py",
):
    p = pathlib.Path(path)
    s = p.read_text(encoding="utf-8")
    n = s.count('"active_account_ids"')
    if n:
        p.write_text(s.replace('"active_account_ids"', '"resolve_scan_account_ids"'), encoding="utf-8")
    total += n
    print(path, n)
print("всего", total)
PY
```
Expected: `tests/unit/test_health_watchdog.py 4`, `tests/integration/test_browser_operation_fence.py 2`, `tests/unit/test_account_timezone_refresh.py 1`, `всего 7`.

- [ ] **Step 7: Убедиться, что unit-тесты проходят**

Run:
```bash
ruff check --fix core/meta_api/account_tz.py && PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/unit/test_account_timezone_refresh.py tests/unit/test_health_watchdog.py -q
```
Expected: PASS, все тесты обоих файлов зелёные.

- [ ] **Step 8: Написать integration-regression на замкнутый круг**

Создать `tests/integration/test_account_context_without_scan.py`:

```python
# -*- coding: utf-8 -*-
"""Новый кабинет получает снимок без единого скана.

Замкнутый круг 17.08.2026: визард требует подтверждённый контекст кабинета,
контекст писал только фоновый refresh, а refresh обходил лишь кабинеты с уже
отсканированными кампаниями. У нового кабинета их нет — и при выключенном
сканировании круг не размыкался никогда.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

import core.meta_api.account_tz as account_tz


class _Client:
    """Живой канал, отвечающий как настоящая Meta по кабинету оффера."""

    async def execute_graph_call(self, **_kwargs):
        return {"timezone_name": "America/Dawson_Creek", "currency": "USD"}


@pytest.mark.asyncio
async def test_configured_cabinet_gets_snapshot_without_any_scan(pg_engine) -> None:
    async with pg_engine.begin() as conn:
        offer_id = (
            await conn.execute(
                text(
                    """
                    INSERT INTO offers (code, name, is_active)
                    VALUES ('SCOPE_TST', 'SCOPE_TST', TRUE)
                    RETURNING id
                    """
                )
            )
        ).scalar_one()
        await conn.execute(
            text("INSERT INTO ad_accounts (account_id) VALUES ('2108857220005012')")
        )
        await conn.execute(
            text(
                """
                INSERT INTO offer_ad_accounts (offer_id, account_id)
                VALUES (:offer_id, '2108857220005012')
                """
            ),
            {"offer_id": offer_id},
        )

    # Ни одной отсканированной кампании: сканирование выключено.
    async with pg_engine.connect() as conn:
        scanned = await conn.scalar(text("SELECT count(*) FROM fb_campaigns"))
    assert scanned == 0

    updated = await account_tz.refresh_account_timezones(pg_engine, _Client())

    assert updated == 1
    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT timezone_name, currency
                    FROM meta_account_snapshot
                    WHERE account_id = '2108857220005012'
                    """
                )
            )
        ).first()
    assert row is not None
    assert row.timezone_name == "America/Dawson_Creek"
    assert row.currency == "USD"
```

- [ ] **Step 9: Прогнать integration на одноразовой БД**

Боевую БД не трогать.

Run:
```bash
docker run --rm -d --name fb-agent-test-db -e POSTGRES_PASSWORD=test -e POSTGRES_USER=test -e POSTGRES_DB=fb_agent_ci_test -p 55432:5432 postgres:16
```
Expected: печатается id контейнера.

Run:
```bash
sleep 9 && TEST_DATABASE_URL="postgresql+asyncpg://test:test@127.0.0.1:55432/fb_agent_ci_test" PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/integration/test_account_context_without_scan.py tests/integration/test_browser_operation_fence.py -q
```
Expected: PASS.

- [ ] **Step 10: Коммит**

```bash
git add core/meta_api/account_tz.py tests/unit/test_account_timezone_refresh.py tests/unit/test_health_watchdog.py tests/integration/test_browser_operation_fence.py tests/integration/test_account_context_without_scan.py
git commit -m "fix(meta-api): скоуп кабинетов берётся из конфигурации, а не из следов сканов"
```

---

### Task 2: Гард против повторения

**Files:**
- Create: `tests/unit/test_account_scope_single_source.py`

**Interfaces:**
- Consumes: результат Task 1 — в `core/` и `apps/` не осталось модулей, выводящих скоуп кабинетов из таблиц сканов.
- Produces: тест, падающий при появлении второго определения скоупа. Ничего не экспортирует.

- [ ] **Step 1: Написать гард**

Создать `tests/unit/test_account_scope_single_source.py`:

```python
# -*- coding: utf-8 -*-
"""Скоуп «наших кабинетов» определяется ровно в одном месте.

17.08.2026 в core/meta_api/account_tz.py завёлся второй ответ на вопрос «какие
кабинеты наши» — DISTINCT по fb_campaigns. Он выглядел безобидно, но замкнул
круг: новый кабинет не получал снимок контекста никогда. Тест ловит любую
попытку снова вывести скоуп из следов сканера.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Таблицы, которые наполняет сканер. Скоуп из них выводить нельзя: это
# производная от работы сканера, а не намерение оператора.
_SCAN_TABLES = ("fb_campaigns", "fb_adsets", "ad_metrics")

# Ловим именно ВЫБОРКУ МНОЖЕСТВА кабинетов. Чтение каталога сканов ради одной
# идентичности (load_ad_account_id_for_fb_ad: кабинет конкретного fb_ad_id)
# сюда не попадает и не нуждается в исключении: там нет DISTINCT. Список
# исключений намеренно отсутствует — вечное исключение ослабило бы гард.
_SCOPE_QUERY = re.compile(
    r"DISTINCT\s+ad_account_id|distinct\(\s*[A-Za-z_.]*ad_account_id",
    re.IGNORECASE,
)


def _python_sources() -> list[Path]:
    files: list[Path] = []
    for folder in ("core", "apps"):
        files.extend(
            path
            for path in (ROOT / folder).rglob("*.py")
            if "__pycache__" not in path.parts
        )
    return files


def test_no_module_derives_cabinet_scope_from_scan_results() -> None:
    offenders: list[str] = []
    for path in _python_sources():
        relative = path.relative_to(ROOT)
        source = path.read_text(encoding="utf-8")
        if not _SCOPE_QUERY.search(source):
            continue
        if any(table in source for table in _SCAN_TABLES):
            offenders.append(str(relative))
    assert offenders == [], (
        "скоуп кабинетов выводится из следов сканера в: "
        + ", ".join(offenders)
        + " — используйте резолвер конфигурации из core/observer/accounts.py"
    )


def test_configured_scope_sql_lives_in_exactly_one_module() -> None:
    """Членство кабинета в оффере читает только каталог."""
    owners = [
        str(path.relative_to(ROOT))
        for path in _python_sources()
        if "OfferAdAccount" in path.read_text(encoding="utf-8")
        and "models" not in path.relative_to(ROOT).parts
    ]
    assert owners == ["core/ad_account_catalog.py"]
```

- [ ] **Step 2: Убедиться, что гард проходит на исправленном коде**

Run:
```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/unit/test_account_scope_single_source.py -q
```
Expected: PASS — после Task 1 нарушителей нет.

- [ ] **Step 3: Убедиться, что гард ловит нарушение**

Временно вернуть дубль и проверить, что тест краснеет.

Run:
```bash
python3 - <<'PY'
import pathlib
p = pathlib.Path("core/meta_api/account_tz.py")
s = p.read_text(encoding="utf-8")
p.write_text(s + '''

async def _temporary_offender(engine):
    async with engine.connect() as conn:
        await conn.execute(text("SELECT DISTINCT ad_account_id FROM fb_campaigns"))
''', encoding="utf-8")
print("нарушитель добавлен")
PY
```
Expected: `нарушитель добавлен`.

Run:
```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/unit/test_account_scope_single_source.py -q
```
Expected: FAIL с сообщением `скоуп кабинетов выводится из следов сканера в: core/meta_api/account_tz.py`.

Run:
```bash
git checkout -- core/meta_api/account_tz.py && PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/unit/test_account_scope_single_source.py -q
```
Expected: PASS — нарушитель убран, гард снова зелёный.

- [ ] **Step 4: Коммит**

```bash
git add tests/unit/test_account_scope_single_source.py
git commit -m "test: скоуп кабинетов определяется ровно в одном месте"
```

---

### Task 3: Имя перестаёт врать

**Files:**
- Modify: `core/ad_account_catalog.py:102`
- Modify: `core/observer/accounts.py:46`
- Modify: все вызывающие модули и тесты (список в Step 3)

**Interfaces:**
- Consumes: результат Task 1 — единственный резолвер конфигурации.
- Produces: `resolve_configured_ad_account_ids(engine: AsyncEngine) -> list[str]` в `core/observer/accounts.py` и `AdAccountCatalog.resolve_configured_set(conn) -> list[str]`. Старые имена `resolve_scan_account_ids` и `resolve_scan_set` исчезают.

Задачу можно отклонить отдельно от Task 1 и Task 2: она не меняет поведение, только устраняет причину, по которой дубль завёлся — имя, обещающее «это про сканирование», хотя множество используют health-проверки, операторский API, создание кампаний и проба канала.

- [ ] **Step 1: Написать падающий тест имени**

Добавить в `tests/unit/test_ad_account_catalog_contract.py` в список методов на строке 46: заменить `"resolve_scan_set",` на `"resolve_configured_set",`.

Добавить в конец того же файла:

```python
# Имя множества определяет, найдут его или напишут своё. «scan» обещало, что
# это внутреннее дело observer'а, и автор meta_api-модуля написал свой запрос
# по fb_campaigns вместо импорта (17.08.2026).
def test_configured_scope_is_not_named_after_scanning() -> None:
    from core import observer

    assert hasattr(observer.accounts, "resolve_configured_ad_account_ids")
    assert not hasattr(observer.accounts, "resolve_scan_account_ids")
    assert not hasattr(AdAccountCatalog, "resolve_scan_set")
```

- [ ] **Step 2: Убедиться, что тест падает**

Run:
```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/unit/test_ad_account_catalog_contract.py -q
```
Expected: FAIL — `assert callable(getattr(AdAccountCatalog, "resolve_configured_set"))` не проходит.

- [ ] **Step 3: Выполнить переименование сплошняком**

Run:
```bash
python3 - <<'PY'
import pathlib
pairs = (
    ("resolve_scan_account_ids", "resolve_configured_ad_account_ids"),
    ("resolve_scan_set", "resolve_configured_set"),
)
total = 0
for folder in ("core", "apps", "tests"):
    for path in pathlib.Path(folder).rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        s = path.read_text(encoding="utf-8")
        out = s
        for old, new in pairs:
            out = out.replace(old, new)
        if out != s:
            path.write_text(out, encoding="utf-8")
            total += 1
            print(path)
print("файлов изменено:", total)
PY
```
Expected: список файлов и итоговое число; среди них обязаны быть `core/ad_account_catalog.py`, `core/observer/accounts.py`, `core/meta_api/account_tz.py`, `core/meta_api/browser_readiness.py`, `apps/observer_worker/main.py`, `apps/api/routers/health.py`, `apps/api/routers/v1/operator.py`, `apps/api/routers/v1/settings_observer.py`.

- [ ] **Step 4: Поправить докстринги, где осталось слово scan**

В `core/observer/accounts.py` заменить докстринг функции:

```python
async def resolve_configured_ad_account_ids(engine: AsyncEngine) -> list[str]:
    """Наши кабинеты: отсортированный DISTINCT-союз кабинетов активных офферов.

    Это намерение оператора, а не производная от работы сканера. Множество
    используют observer, health-проверки, операторский API, создание кампаний
    и проба готовности канала — «scan» в имени вводило в заблуждение и однажды
    уже привело к дублирующему запросу по fb_campaigns (17.08.2026).
    """
```

В `core/ad_account_catalog.py` заменить докстринг метода:

```python
    async def resolve_configured_set(self, conn: AsyncConnection) -> list[str]:
        """Return the sorted union of accounts linked to active offers."""
```

- [ ] **Step 5: Проверить, что старых имён не осталось**

Run:
```bash
grep -rn "resolve_scan_account_ids\|resolve_scan_set" --include="*.py" core/ apps/ tests/ | head
```
Expected: пустой вывод.

- [ ] **Step 6: Прогнать unit-тесты**

Run:
```bash
ruff check --fix core/ apps/ tests/ && PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/unit -q
```
Expected: PASS без новых падений.

- [ ] **Step 7: Обновить гард из Task 2 под новое имя**

В `tests/unit/test_account_scope_single_source.py` в тексте assert'а заменить

```python
        + " — используйте резолвер конфигурации из core/observer/accounts.py"
```

на

```python
        + " — используйте resolve_configured_ad_account_ids из core/observer/accounts.py"
```

- [ ] **Step 8: Коммит**

```bash
git add -A core apps tests
git commit -m "refactor: скоуп кабинетов называется по назначению, а не по сканированию"
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

- [ ] **Step 3: Контракт не поехал**

Run:
```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/export_openapi.py && pnpm run format:openapi && pnpm gen:api && git status --porcelain frontend/openapi.json packages/shared/src/api/generated.ts
```
Expected: пустой вывод — правки не трогали ни схемы, ни докстринги ручек.

- [ ] **Step 4: Проверка на проде после выката**

Убедиться, что снимок появляется для кабинета оффера без единого скана:

```bash
ssh root@62.60.150.133 "docker exec -i fb_agent_infra-postgres-1 psql -U fb_stop_bot -d fb_stop_bot -c 'select account_id, timezone_name, currency, currency_observed_at from meta_account_snapshot'"
```
Expected: строки по кабинетам активных офферов с непустыми `timezone_name` и `currency`, при `fb_campaigns` = 0 строк.

---

## Что требуется от владельца

1. **Решение по Task 3.** Переименование не меняет поведение, но трогает восемь модулей и семь тестовых файлов. Скажи, делать его вместе с Task 1-2 или отложить.
2. **Запустить деплой.** Push в `main` не выкатывает: нужен ручной `workflow_dispatch` на workflow `Release`.
3. **Посмотреть глазами.** После выката открыть визард и убедиться, что контекст кабинета подтверждается без включения сканирования.

## Что план сознательно не делает

- Не убирает дефолт «`account_ids=None` означает все наши кабинеты». Неявный глобальный скоуп — сам по себе спорная штука, но на него опираются операторский снимок и аналитика, и его снятие — отдельная работа с другим набором рисков.
- Не трогает `load_ad_account_id_for_fb_ad`: чтение `fb_ads → fb_adsets → fb_campaigns` там законно, это идентичность одного объявления, а не скоуп.
- Не добавляет чистку `meta_account_snapshot` при деактивации оффера: устаревшее доказательство никому не мешает, а его удаление — необратимая операция без запроса.
- Не меняет правило блокировки залива: без подтверждённого снимка кампания по-прежнему не запускается.
