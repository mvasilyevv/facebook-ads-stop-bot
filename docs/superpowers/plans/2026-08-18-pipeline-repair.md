# Пайплайн перестаёт стоить полчаса за чужую ошибку — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Зависший шаг падает за минуты, а не за часы; всё, что гоняет CI, можно прогнать одной локальной командой до пуша; упавший шаг канала стола говорит оператору, что делать.

**Architecture:** План не переписывает пайплайн — он делает ровно то, ради чего существует, и сегодня дважды не пустил в прод сломанное. План закрывает три конкретные дыры, каждая из которых сегодня стоила отдельного цикла: у verify-джоб нет своих таймаутов, локально нельзя прогнать то же, что гоняет CI, и диагноз упавшего деплоя приходится добывать вручную.

**Tech Stack:** GitHub Actions (composite workflow `verify.yml` + `release.yml`), Python 3 (fbctl, pytest), pnpm workspace, Playwright.

## Global Constraints

- Гейты не ослабляются: ни один шаг проверки не удаляется и не переводится в `continue-on-error`. План меняет только сроки, кэш и тексты.
- Money-путь не трогается: `docker-rehearsal`, `deploy` и порядок шагов `fbctl` остаются как есть.
- Никогда не запускать pytest против боевой БД (`:5433`): integration-фикстуры сносят `offers`/`offer_rules`. Только одноразовый контейнер.
- Raw exception, traceback, UUID и секреты не попадают в operator UI, Telegram, URL и логи.
- Тексты для человека — по-русски; имена шагов, workflow и технические идентификаторы — английские.
- Контракт OpenAPI не меняется.

---

## Что сломалось сегодня

Пять прогонов `Release` 18.08.2026, разобраны по логам.

| Прогон | Итог | Причина | Чья |
|---|---|---|---|
| `32110428248` | success (22м45с) | — | — |
| `32171505061` | failure (28м14с) | `ensure_desktop_channel`: Vision не поднимал браузер профиля | окружение |
| `32175430919` | отменён мной на 30-й минуте | джоба `platform` висела на `apt-get install ripgrep shellcheck` | пайплайн |
| `32178307944` | отменён мной | свернул цикл, чтобы не выкатывать дважды | — |
| `32178908051` | failure (6м23с) | Playwright: ожидание строки `"$ · Africa/Accra · контроль кабинета"` после правки `$` → `USD` | моя |

### Дыра 1 — у verify-джоб нет таймаутов

`.github/workflows/verify.yml`: ни у одной из четырёх джоб (`backend`, `frontend`,
`ui-evidence`, `platform`) нет `timeout-minutes`. Дефолт GitHub — 360 минут.
`docker-rehearsal` в `release.yml:152` таймаут имеет (30), шаги деплоя — тоже (25).
Сегодня зависший `apt-get` шёл 29 минут и остановился только потому, что я
отменил прогон руками.

Замеры нормального времени по сегодняшним зелёным прогонам: `platform` — 20 с,
`backend` — 3 м 25 с, `frontend` — 4 м 7 с, `ui-evidence` — 6 м 17 с (успешный
прогон 13 м 35 с при холодной установке браузеров).

### Дыра 2 — локально нельзя прогнать то же, что в CI

CI гоняет четырнадцать проверок, разложенных по четырём джобам. Корневые скрипты
`package.json` покрывают четыре из них (`lint`, `typecheck`, `test`, `build`), а
`Makefile` — только локальный контур. Playwright, Storybook a11y, actionlint,
shellcheck, `validate-platform-configs.sh`, `validate_executable_modes.py`,
drift-гейт gRPC-стабов и docker-сборки фронтов запускать локально нечем — их
надо помнить.

Сегодня я прогнал `pnpm -r test` и решил, что фронт проверен. Playwright живёт
отдельной джобой, в `pnpm -r test` не входит — правка строки доехала до CI и
стоила шести минут прогона и одного цикла.

### Дыра 3 — упавший шаг канала стола не говорит, что делать

`fbctl/controller.py:983` ждёт 180 секунд и падает с текстом, который пришёл от
ручки:

```
timed out waiting for recovered browser channel: browser channel is not ready
(Profile restart completed but the channel is not ready)
```

Что именно случилось и что делать оператору — в сообщении нет. Сегодня диагноз
(«Vision не запускает браузер профиля, зайди на стол через RustDesk и подними
его») пришлось добывать шестью ssh-запросами. При этом шаг уже знает, что
деплой остановлен и money-воркеры лежат.

---

## File Structure

| Файл | Ответственность | Изменение |
|---|---|---|
| `.github/workflows/verify.yml` | Четыре джобы проверки | `timeout-minutes` на каждой + кэш браузеров Playwright |
| `scripts/preflight.sh` | Локальный прогон «как в CI» | создать |
| `tests/unit/test_preflight_matches_ci.py` | Гард: локальный прогон не отстаёт от CI | создать |
| `fbctl/controller.py` | Шаги деплоя | текст отказа шага канала стола |
| `tests/unit/test_fbctl.py` | Контракт fbctl | +тест текста отказа |
| `CLAUDE.md` | Карта команд репозитория | строка про `scripts/preflight.sh` |

---

### Task 1: Зависшая джоба падает за минуты

**Files:**
- Modify: `.github/workflows/verify.yml:21`, `:93`, `:145`, `:189`
- Create: `tests/unit/test_ci_timeouts.py`

**Interfaces:**
- Consumes: ничего из предыдущих задач.
- Produces: каждая джоба `verify.yml` имеет `timeout-minutes`. Task 2 опирается на то, что файл остаётся валидным YAML с тем же составом джоб.

- [ ] **Step 1: Написать падающий гард**

Создать `tests/unit/test_ci_timeouts.py`:

```python
# -*- coding: utf-8 -*-
"""У каждой джобы проверки есть свой предел времени.

18.08.2026 джоба platform зависла на `apt-get install` и шла 29 минут — её
остановил человек, а не пайплайн. Дефолт GitHub — 360 минут: без явного
timeout-minutes сетевой затык стоит рабочего дня, а не пары минут.
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
_VERIFY = ROOT / ".github/workflows/verify.yml"

# Потолок вдвое выше самого медленного наблюдённого прогона: он ловит зависание,
# но не рубит честную джобу на холодном кэше.
_MAX_TIMEOUT_MINUTES = 40


def _verify_jobs() -> dict:
    document = yaml.safe_load(_VERIFY.read_text(encoding="utf-8"))
    return document["jobs"]


def test_every_verify_job_declares_a_timeout() -> None:
    missing = [name for name, job in _verify_jobs().items() if "timeout-minutes" not in job]
    assert missing == [], (
        "джобы без timeout-minutes: "
        + ", ".join(missing)
        + " — без предела зависший шаг идёт до лимита раннера в 360 минут"
    )


def test_verify_timeouts_stay_tight() -> None:
    too_long = {
        name: job["timeout-minutes"]
        for name, job in _verify_jobs().items()
        if int(job.get("timeout-minutes", 0)) > _MAX_TIMEOUT_MINUTES
    }
    assert too_long == {}, f"предел выше {_MAX_TIMEOUT_MINUTES} минут не ловит зависание: {too_long}"
```

- [ ] **Step 2: Убедиться, что гард падает**

Run:
```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/unit/test_ci_timeouts.py -q
```
Expected: FAIL — `джобы без timeout-minutes: backend, frontend, ui-evidence, platform`.

- [ ] **Step 3: Проставить пределы**

В `.github/workflows/verify.yml` добавить строку `timeout-minutes` сразу после
`runs-on: ubuntu-latest` в каждой из четырёх джоб.

Для `backend` (наблюдалось 3 м 25 с):

```yaml
  backend:
    name: Backend, schema and OpenAPI
    runs-on: ubuntu-latest
    # Наблюдалось 3-4 минуты; предел ловит зависшую установку зависимостей,
    # а не медленный прогон (инцидент 18.08.2026 — 29 минут на apt-get).
    timeout-minutes: 20
```

Для `frontend` (наблюдалось 4 м 7 с):

```yaml
  frontend:
    name: Frontend, generated API and browser-agent
    runs-on: ubuntu-latest
    timeout-minutes: 25
```

Для `ui-evidence` (наблюдалось до 13 м 35 с на холодной установке браузеров):

```yaml
  ui-evidence:
    name: Storybook a11y and Playwright
    runs-on: ubuntu-latest
    timeout-minutes: 30
```

Для `platform` (наблюдалось 20 с):

```yaml
  platform:
    name: Workflow, entrypoint and shell contracts
    runs-on: ubuntu-latest
    timeout-minutes: 10
```

- [ ] **Step 4: Убедиться, что гард проходит**

Run:
```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/unit/test_ci_timeouts.py -q
```
Expected: PASS.

- [ ] **Step 5: Проверить синтаксис workflow тем же линтером, что и CI**

Run:
```bash
docker run --rm --volume "$PWD:/repo:ro" --workdir /repo rhysd/actionlint@sha256:887a259a5a534f3c4f36cb02dca341673c6089431057242cdc931e9f133147e9 -color
```
Expected: без вывода и с кодом 0.

- [ ] **Step 6: Коммит**

```bash
git add .github/workflows/verify.yml tests/unit/test_ci_timeouts.py
git commit -m "ci: у каждой джобы проверки появился собственный предел времени"
```

---

### Task 2: Браузеры Playwright не качаются заново каждый прогон

**Files:**
- Modify: `.github/workflows/verify.yml:164-168`

**Interfaces:**
- Consumes: Task 1 — `ui-evidence` уже имеет `timeout-minutes: 30`.
- Produces: ничего для последующих задач.

- [ ] **Step 1: Добавить кэш перед установкой браузеров**

В `.github/workflows/verify.yml` в джобе `ui-evidence` между шагом
`Install frontend dependencies` и шагом `Install browser runtimes` вставить:

```yaml
      # Три браузера тянутся заново каждый прогон и занимают минуты. Ключ
      # привязан к версии Playwright из lock-файла: смена версии — новый кэш.
      - name: Cache Playwright browsers
        id: playwright-cache
        uses: actions/cache@0057852bfaa89a56745cba8c7296529d2fc39830 # v4
        with:
          path: ~/.cache/ms-playwright
          key: playwright-${{ runner.os }}-${{ hashFiles('pnpm-lock.yaml') }}
```

- [ ] **Step 2: Ставить только системные зависимости при попадании в кэш**

Заменить шаг

```yaml
      - name: Install browser runtimes
        run: pnpm --filter fb-stop-bot-frontend exec playwright install --with-deps chromium firefox webkit
```

на

```yaml
      - name: Install browser runtimes
        run: |
          if [ "${{ steps.playwright-cache.outputs.cache-hit }}" = "true" ]; then
            # Бинарники уже в кэше; системные библиотеки живут в образе раннера
            # и кэшу не подлежат, поэтому ставим их отдельно.
            pnpm --filter fb-stop-bot-frontend exec playwright install-deps chromium firefox webkit
          else
            pnpm --filter fb-stop-bot-frontend exec playwright install --with-deps chromium firefox webkit
          fi
```

- [ ] **Step 3: Проверить синтаксис workflow**

Run:
```bash
docker run --rm --volume "$PWD:/repo:ro" --workdir /repo rhysd/actionlint@sha256:887a259a5a534f3c4f36cb02dca341673c6089431057242cdc931e9f133147e9 -color
```
Expected: без вывода и с кодом 0.

- [ ] **Step 4: Коммит**

```bash
git add .github/workflows/verify.yml
git commit -m "ci: браузеры Playwright переиспользуются между прогонами"
```

---

### Task 3: Одна локальная команда гоняет то же, что CI

**Files:**
- Create: `scripts/preflight.sh`
- Create: `tests/unit/test_preflight_matches_ci.py`
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: ничего из предыдущих задач.
- Produces: `scripts/preflight.sh` — исполняемый скрипт с флагом `--fast`. Гард `tests/unit/test_preflight_matches_ci.py` требует, чтобы каждая команда из `verify.yml` встречалась в скрипте.

- [ ] **Step 1: Написать падающий гард**

Создать `tests/unit/test_preflight_matches_ci.py`:

```python
# -*- coding: utf-8 -*-
"""Локальный прогон не отстаёт от CI.

18.08.2026 правка одной строки интерфейса сломала Playwright. Локально был
прогнан `pnpm -r test`, который Playwright не включает, — поломка доехала до CI
и стоила цикла. Гард требует, чтобы каждая проверка из verify.yml имела
соответствие в scripts/preflight.sh.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_PREFLIGHT = ROOT / "scripts/preflight.sh"

# Команды, которые CI гоняет в verify.yml. Совпадение проверяется по подстроке:
# скрипт волен обернуть команду, но не волен её потерять.
_CI_COMMANDS = (
    "scripts/generate_grpc_stubs.py",
    "ruff check .",
    "scripts.run-migrations-locked",
    "pytest tests/",
    "pnpm run sync:api",
    "pnpm lint",
    "pnpm typecheck",
    "pnpm test",
    "Dockerfile.frontend",
    "Dockerfile.mini-app",
    "npm run lint --prefix services/browser-agent",
    "npm test --prefix services/browser-agent",
    "build-storybook",
    "test:storybook",
    "test:e2e",
    "actionlint",
    "validate_executable_modes.py",
    "validate-platform-configs.sh",
    "shellcheck",
)


def test_preflight_script_exists_and_is_executable() -> None:
    assert _PREFLIGHT.exists(), "нет scripts/preflight.sh — локально нечем прогнать то же, что в CI"
    assert _PREFLIGHT.stat().st_mode & 0o111, "scripts/preflight.sh не исполняемый"


def test_preflight_covers_every_ci_command() -> None:
    source = _PREFLIGHT.read_text(encoding="utf-8")
    missing = [command for command in _CI_COMMANDS if command not in source]
    assert missing == [], (
        "локальный прогон не покрывает проверки CI: "
        + ", ".join(missing)
        + " — их поломку увидит только CI, через двадцать минут"
    )
```

- [ ] **Step 2: Убедиться, что гард падает**

Run:
```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/unit/test_preflight_matches_ci.py -q
```
Expected: FAIL — `нет scripts/preflight.sh`.

- [ ] **Step 3: Написать скрипт**

Создать `scripts/preflight.sh`:

```bash
#!/usr/bin/env bash
# Локальный прогон тех же проверок, что гоняет .github/workflows/verify.yml.
#
# Зачем: 18.08.2026 правка строки интерфейса сломала Playwright, а локально был
# прогнан только `pnpm -r test` — поломка доехала до CI и стоила цикла. Здесь
# собрано всё, что CI проверяет, чтобы это можно было увидеть до пуша.
#
#   scripts/preflight.sh          полный прогон (примерно 12-15 минут)
#   scripts/preflight.sh --fast   без Playwright, Storybook и docker-сборок
#
# Полный прогон требует Docker: actionlint и сборки фронтов идут в контейнерах.
set -euo pipefail

cd "$(dirname "$0")/.."

FAST=0
if [ "${1:-}" = "--fast" ]; then
  FAST=1
fi

PYTHON="${PYTHON:-.venv/bin/python}"
export PYTHONDONTWRITEBYTECODE=1

step() {
  printf '\n\033[1m==> %s\033[0m\n' "$1"
}

step "Backend: сгенерированные gRPC-стабы не разошлись с proto"
"$PYTHON" -B scripts/generate_grpc_stubs.py
git diff --exit-code -- clients/python_grpc

step "Backend: ruff"
ruff check .

step "Backend: миграции с чистой базы и дрейф ORM"
"$PYTHON" -m scripts.run-migrations-locked

step "Backend: pytest tests/"
"$PYTHON" -m pytest tests/ --timeout=30 -q

step "Контракт: pnpm run sync:api без дрейфа"
PYTHON="$PYTHON" pnpm run sync:api
git diff --exit-code -- frontend/openapi.json packages/shared/src/api/generated.ts

step "Фронт: pnpm lint"
pnpm lint

step "Фронт: pnpm typecheck"
pnpm typecheck

step "Фронт: pnpm test"
pnpm test

step "Browser-agent: lint и тесты"
npm run lint --prefix services/browser-agent
npm test --prefix services/browser-agent

step "Платформа: права на исполняемые точки входа"
python3 scripts/validate_executable_modes.py scripts/fbctl scripts/validate-platform-configs.sh

step "Платформа: встроенный fbctl и Compose"
./scripts/validate-platform-configs.sh

step "Платформа: shellcheck"
find scripts -maxdepth 1 -type f -name '*.sh' -print0 | sort -z | xargs -0 shellcheck

if [ "$FAST" = "1" ]; then
  printf '\n\033[1mБыстрый прогон закончен. Playwright, Storybook и docker-сборки пропущены.\033[0m\n'
  exit 0
fi

step "Платформа: actionlint"
docker run --rm --volume "$PWD:/repo:ro" --workdir /repo \
  rhysd/actionlint@sha256:887a259a5a534f3c4f36cb02dca341673c6089431057242cdc931e9f133147e9 -color

step "Фронт: docker-сборки"
docker build --target builder --file docker/Dockerfile.frontend .
docker build --target builder --file docker/Dockerfile.mini-app .

step "UI: Storybook и доступность"
pnpm --filter fb-stop-bot-frontend build-storybook
pnpm --filter fb-stop-bot-frontend test:storybook

step "UI: Playwright"
pnpm --filter fb-stop-bot-frontend test:e2e

printf '\n\033[1mВсё, что гоняет CI, прошло локально.\033[0m\n'
```

- [ ] **Step 4: Сделать скрипт исполняемым**

Run:
```bash
chmod +x scripts/preflight.sh
```
Expected: без вывода.

- [ ] **Step 5: Убедиться, что гард проходит**

Run:
```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/unit/test_preflight_matches_ci.py -q
```
Expected: PASS.

- [ ] **Step 6: Проверить, что скрипт проходит shellcheck**

Run:
```bash
shellcheck scripts/preflight.sh
```
Expected: без вывода.

- [ ] **Step 7: Прогнать быстрый режим целиком**

Боевую БД не трогать: `pytest tests/` внутри скрипта ходит в базу из `.env`,
поэтому перед прогоном поднять одноразовый контейнер и указать его.

Run:
```bash
docker rm -f fb-agent-test-db 2>/dev/null; docker run --rm -d --name fb-agent-test-db -e POSTGRES_PASSWORD=test -e POSTGRES_USER=test -e POSTGRES_DB=fb_agent_ci_test -p 55432:5432 postgres:16
```
Expected: печатается id контейнера.

Run:
```bash
sleep 9 && TEST_DATABASE_URL="postgresql+asyncpg://test:test@127.0.0.1:55432/fb_agent_ci_test" scripts/preflight.sh --fast
```
Expected: все шаги до строки «Быстрый прогон закончен» проходят.

Run:
```bash
docker rm -f fb-agent-test-db
```
Expected: печатается имя контейнера.

- [ ] **Step 8: Записать команду в карту репозитория**

В `CLAUDE.md` в разделе `## Команды` добавить перед блоком `# Backend`:

```markdown
```bash
# Всё, что гоняет CI, одной командой (перед пушем)
scripts/preflight.sh          # полный прогон, ~12-15 минут
scripts/preflight.sh --fast   # без Playwright, Storybook и docker-сборок
```
```

- [ ] **Step 9: Коммит**

```bash
git add scripts/preflight.sh tests/unit/test_preflight_matches_ci.py CLAUDE.md
git commit -m "ci: локальный прогон повторяет проверки CI одной командой"
```

---

### Task 4: Упавший шаг канала стола говорит, что делать

**Files:**
- Modify: `fbctl/controller.py:968-990`
- Modify: `tests/unit/test_fbctl.py`

**Interfaces:**
- Consumes: ничего из предыдущих задач.
- Produces: `_ensure_desktop_channel` при исчерпании ожидания поднимает исключение с текстом, содержащим действие оператора.

- [ ] **Step 1: Написать падающий тест**

Добавить в конец `tests/unit/test_fbctl.py`:

```python
# 18.08.2026 деплой упал с текстом «timed out waiting for recovered browser
# channel: browser channel is not ready (Profile restart completed but the
# channel is not ready)». Что делать оператору — в сообщении не было, и диагноз
# добывали вручную шестью ssh-запросами.
def test_desktop_channel_failure_names_the_operator_action() -> None:
    import inspect

    from fbctl import controller

    source = inspect.getsource(controller._ensure_desktop_channel)
    assert "RustDesk" in source, "в отказе нет канала доступа к столу"
    assert "профил" in source, "в отказе не сказано, что поднимать"
```

- [ ] **Step 2: Убедиться, что тест падает**

Run:
```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/unit/test_fbctl.py -q -k desktop_channel_failure
```
Expected: FAIL — `в отказе нет канала доступа к столу`.

- [ ] **Step 3: Обернуть ожидание понятным отказом**

В `fbctl/controller.py` заменить тело `_ensure_desktop_channel` (строки 980-990) на:

```python
        api = self._api_origin(config)
        require_ok_status(self.probes, f"{api}/healthz")
        require_ok_status(self.probes, f"{api}/readyz")
        try:
            wait_for(
                "recovered browser channel",
                lambda: ensure_browser_channel(self.probes, api, config.api_key),
                timeout=180,
                interval=5,
                monotonic=self.monotonic,
                sleep=self.sleep,
            )
        except Exception as exc:
            # Технический текст ручки не говорит, что делать. Чаще всего Vision
            # не поднимает браузер профиля после пересоздания стола, и починить
            # это может только человек у экрана (инцидент 18.08.2026: диагноз
            # добывали вручную, пока money-воркеры лежали).
            raise RuntimeError(
                f"{exc}. Похоже, Vision не поднял браузер профиля после "
                "пересоздания стола: зайди на стол через RustDesk, запусти "
                "профиль и проверь, что Facebook залогинен, затем повтори "
                "деплой — он идемпотентен."
            ) from exc
```

- [ ] **Step 4: Убедиться, что тест проходит**

Run:
```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/unit/test_fbctl.py -q
```
Expected: PASS, включая существующие тесты контроллера.

- [ ] **Step 5: Проверить, что встроенный fbctl остался валидным**

Run:
```bash
./scripts/validate-platform-configs.sh
```
Expected: без ошибок.

- [ ] **Step 6: Коммит**

```bash
git add fbctl/controller.py tests/unit/test_fbctl.py
git commit -m "fix(deploy): отказ шага канала стола называет действие оператора"
```

---

### Task 5: Полные гейты

**Files:**
- Изменений кода нет; задача — доказательства.

**Interfaces:**
- Consumes: всё, что сделано в Task 1-4.
- Produces: зелёные гейты.

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

- [ ] **Step 3: Платформа**

Run:
```bash
docker run --rm --volume "$PWD:/repo:ro" --workdir /repo rhysd/actionlint@sha256:887a259a5a534f3c4f36cb02dca341673c6089431057242cdc931e9f133147e9 -color
```
Expected: без вывода и с кодом 0.

Run:
```bash
find scripts -maxdepth 1 -type f -name '*.sh' -print0 | sort -z | xargs -0 shellcheck
```
Expected: без вывода.

- [ ] **Step 4: Проверка на живом прогоне**

После пуша посмотреть прогон `Release` от push и убедиться, что:
- джобы показывают выставленные пределы времени;
- шаг `Cache Playwright browsers` во втором прогоне даёт `cache-hit: true`, и
  `ui-evidence` заканчивается быстрее первого прогона.

---

## Что требуется от владельца

1. **Запустить деплой** после мержа: push в `main` не выкатывает, нужен ручной `workflow_dispatch` на workflow `Release`.
2. **Решение по Task 2.** Кэш браузеров экономит минуты каждого прогона, но добавляет ключ, который надо чистить при странных падениях Playwright. Скажи, делать его или оставить холодную установку.

## Что план сознательно не делает

- **Не убирает прогон `Release` на каждый push.** Он выглядит дублем ручного запуска, но именно он собирает образы заранее и гоняет rehearsal до того, как ты нажмёшь деплой. Условие `deploy` уже требует `workflow_dispatch`, так что прод от push не меняется, а `concurrency` в `release.yml:22` отменяет устаревшие push-прогоны. Экономия здесь возможна, но она меняет money-путь и заслуживает отдельного разговора.
- **Не трогает шарды `docker-rehearsal`.** Пять параллельных шардов с таймаутом 30 минут — осознанная настройка с комментарием в коде; сегодня они не падали.
- **Не добавляет автоматический повтор упавших шагов.** Повтор скрыл бы настоящую причину: сегодня оба падения были содержательными, а не флаки.
- **Не чинит саму причину падения `ensure_desktop_channel`** — то, что Vision не поднимает браузер после пересоздания стола. Это отдельная работа по столу, а не по пайплайну; Task 4 лишь делает диагноз читаемым.
