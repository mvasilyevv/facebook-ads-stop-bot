# Пайплайн гоняет затронутое — план

> **Для агентов:** ОБЯЗАТЕЛЬНАЯ ПОД-СКИЛЛ: superpowers:subagent-driven-development
> или superpowers:executing-plans. Шаги помечены чекбоксами (`- [ ]`).

**Цель:** обычный пуш перестаёт платить полную цену релиза. Ручной релиз платит
её всегда.

**Архитектура:** фильтруется только то, чей вход действительно локализован —
`ui-evidence` и четыре шарда `failpoints` репетиции. Всё остальное идёт всегда.
Решение «гонять или нет» принимается ДО вызова `verify.yml` и передаётся явным
входом, а не выводится из контекста внутри вызванного workflow.

**Технологии:** GitHub Actions, bash, Python 3.12, pytest, PyYAML.

## Почему не «фильтруем всё по путям»

Разведка тремя агентами 19.08.2026 показала, что в этом репозитории
«затронутое» путями не выражается. Факты, каждый проверен чтением кода:

- Джоба `backend` — репозиторный линтер, а не тесты бэкенда. `ruff check .`
  линтит весь репозиторий (в `pyproject.toml` нет `include`, только два
  `extend-exclude`). Около тридцати тестов читают `.github/workflows/*`,
  `fbctl/**`, `deploy/**`, `docker/**`, `services/browser-agent/src/**`,
  `frontend/src/**`, `packages/**`, `Makefile`, `CLAUDE.md`. Ссылок на `deploy/`
  в тестах больше (36), чем на `core/` (17).
  `test_platform_validator_leaves_worktree_and_python_cache_unchanged` прямо
  ВЫПОЛНЯЕТ `scripts/validate-platform-configs.sh` подпроцессом.
- `test_cumulative_metric_query_contract.py` сканирует `packages/` — money-гард
  против наивного `SUM` по кумулятивным `ad_metrics` живёт в бэкендовой джобе, а
  его вход включает TS-код фронта.
- Джоба `frontend` гоняет `sync:api`, а `scripts/export_openapi.py` делает
  `from apps.api.main import create_app` — правка бэкенда её вход. Тесты
  browser-agent грузят `proto/v1/*.proto` в рантайме.
- Фильтрация ВНУТРИ `verify.yml` опасна: `release.yml` проверяет
  `needs.verify.result == 'success'`, и этот результат не отличает «прошли все
  четыре джобы» от «пропустили все четыре». Коммит с правкой одной документации
  получил бы зелёный `verify` и уехал бы на прод без единой проверки.
- `images` фильтровать не нужно и вредно: контент-адресация в
  `scripts/ci_image_plan.py` уже даёт корректный пропуск (probe существующего
  тега — секунды), а отказ от прогонов остудит buildx-кэш, и релиз впервые за
  неделю соберёт образы вхолодную под пределами 45 и 60 минут.

## Global Constraints

- Код, комментарии и тексты — по-русски; технические идентификаторы английские.
- **Сомнение всегда в пользу лишней проверки.** Не удалось вычислить состав
  изменений (первый пуш ветки, force-push, `before` из сорока нулей,
  недостижимый коммит) — гоним всё. Пропуск по ошибке недопустим, лишний
  прогон — приемлемая цена.
- Ручной релиз (`workflow_dispatch`) гоняет ВСЁ и всегда. Это выражается
  явным входом `full_run`, а не выводом из `github.event_name` внутри
  вызванного workflow.
- Сначала падающий гард, потом правка.
- `skipped` никогда не приравнивается к успеху. Образец правильной обработки —
  `images.if` в `release.yml`: skipped принимается только там, где доказано, что
  джоба и не должна была идти.
- Джоба с `uses:` не принимает `timeout-minutes`, `env`, `continue-on-error`,
  `steps`, `runs-on`. Допустимы: `name`, `uses`, `with`, `secrets`, `strategy`,
  `needs`, `if`, `concurrency`, `permissions`.
- Внешние `uses:` закрепляются по 40-символьному SHA — это требует гард
  `test_every_external_workflow_dependency_is_immutable`.
- Ветку не создавать: коммиты прямо в `main`, `Co-Authored-By: Claude Opus 5
  <noreply@anthropic.com>`.
- Коммитить только файлы своей задачи. В дереве лежат чужие изменения
  (`frontend/e2e/__audit__/1440px/*.png`) — не трогать, не коммитить.
- ЗАПРЕТ БЕЗ ИСКЛЮЧЕНИЙ: `git stash` не вызывать — в репозитории чужой
  stash-entry от другой сессии.
- Окружение: `.venv/bin/pytest`, `.venv/bin/ruff`, всегда с
  `PYTHONDONTWRITEBYTECODE=1`.
- Прогнать `tests/integration` локально нечем (нужна БД, Docker не поднят) —
  покрытые модули вычитывать глазами и докладывать вердикт.

---

### Task 1: Детектор изменений, который умеет сдаваться

**Files:**
- Create: `scripts/ci_changed_areas.py`
- Create: `tests/unit/test_ci_changed_areas.py`

**Interfaces:**
- Produces: `python3 scripts/ci_changed_areas.py --base <sha> --head <sha>`
  печатает строки `ключ=true|false` для ключей `ui`, `bundle`, пригодные для
  дописывания в `$GITHUB_OUTPUT`. При `--assume-all` или при неразрешимой базе
  печатает все ключи как `true`. Задачи 2 и 3 зовут именно этот скрипт.

**Контекст.** Ключевое свойство — fail-open. Скрипт обязан честно сказать
«не знаю» и вернуть «гоняем всё», а не угадывать.

Области ровно две, потому что фильтруется ровно две вещи:
- `ui` — вход `ui-evidence`: `frontend/`, `packages/features/`,
  `packages/operator-api/`, `packages/operator-ui/`, `packages/shared/`,
  `pnpm-lock.yaml`, `pnpm-workspace.yaml`, `package.json`, `tsconfig.base.json`.
- `bundle` — вход шардов `failpoints`: `fbctl/`, `scripts/fbctl`,
  `tests/rehearsal/`, `deploy/compose/`, `deploy/caddy/`,
  `deploy/systemd/caddy-fb-agent-env.conf`.

- [ ] **Шаг 1: Написать падающие тесты**

Создать `tests/unit/test_ci_changed_areas.py`:

```python
# -*- coding: utf-8 -*-
"""Детектор изменений сдаётся в пользу лишнего прогона.

Пропуск проверки по ошибке дороже лишнего прогона: пропущенная проверка
означает, что дефект едет дальше молча. Поэтому любая неопределённость —
первый пуш ветки, force-push, недостижимая база — обязана давать «гоняем всё».
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = ROOT / "scripts/ci_changed_areas.py"

_ALL_KEYS = ("ui", "bundle")


def _run(*args: str) -> dict[str, str]:
    result = subprocess.run(
        [sys.executable, "-B", str(_SCRIPT), *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    parsed: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            parsed[key] = value
    return parsed


def test_assume_all_marks_every_area_changed() -> None:
    assert _run("--assume-all") == {key: "true" for key in _ALL_KEYS}


def test_unresolvable_base_falls_back_to_running_everything() -> None:
    # Первый пуш ветки приходит с базой из сорока нулей.
    assert _run("--base", "0" * 40, "--head", "HEAD") == {key: "true" for key in _ALL_KEYS}


def test_missing_base_falls_back_to_running_everything() -> None:
    assert _run("--base", "", "--head", "HEAD") == {key: "true" for key in _ALL_KEYS}


def test_every_key_is_always_printed() -> None:
    # Отсутствующий ключ в $GITHUB_OUTPUT даёт пустую строку, а пустая строка
    # не равна 'true' — то есть молчание детектора выключило бы проверку.
    printed = _run("--assume-all")
    assert sorted(printed) == sorted(_ALL_KEYS)


def test_documentation_only_change_touches_no_area() -> None:
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()
    assert _run("--base", head, "--head", head) == {key: "false" for key in _ALL_KEYS}
```

- [ ] **Шаг 2: Убедиться, что тесты красные**

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/unit/test_ci_changed_areas.py -q
```
Ожидается: падение — скрипта ещё нет.

- [ ] **Шаг 3: Написать скрипт**

Создать `scripts/ci_changed_areas.py`:

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Печатает, какие области затронуты диапазоном коммитов.

Пропуск проверки по ошибке дороже лишнего прогона, поэтому неопределённость
всегда разрешается в пользу «гоняем всё»: неизвестная база, база из сорока
нулей, недостижимый коммит, сбой git — любой из этих случаев печатает true.

Областей ровно две — ровно столько, сколько проверок в этом репозитории имеют
локализованный вход. Остальные джобы читают весь репозиторий и фильтрации не
подлежат: подробности в docs/superpowers/plans/2026-08-19-pipeline-affected-only.md.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Префиксы и точные имена, попадание в которые делает область затронутой.
AREAS: dict[str, tuple[str, ...]] = {
    "ui": (
        "frontend/",
        "packages/features/",
        "packages/operator-api/",
        "packages/operator-ui/",
        "packages/shared/",
        "pnpm-lock.yaml",
        "pnpm-workspace.yaml",
        "package.json",
        "tsconfig.base.json",
    ),
    "bundle": (
        "fbctl/",
        "scripts/fbctl",
        "tests/rehearsal/",
        "deploy/compose/",
        "deploy/caddy/",
        "deploy/systemd/caddy-fb-agent-env.conf",
    ),
}

_EMPTY_SHA = "0" * 40


def _print(values: dict[str, bool]) -> None:
    for key in AREAS:
        print(f"{key}={'true' if values.get(key) else 'false'}")


def _changed_files(base: str, head: str) -> list[str] | None:
    """Список изменённых файлов или None, если диапазон неразрешим."""
    if not base or not head or base.strip(_EMPTY_SHA[0]) == "" or base == _EMPTY_SHA:
        return None
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", f"{base}", f"{head}"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except (subprocess.CalledProcessError, OSError):
        return None
    return [line for line in result.stdout.splitlines() if line]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--assume-all", action="store_true")
    args = parser.parse_args()

    if args.assume_all:
        _print({key: True for key in AREAS})
        return 0

    files = _changed_files(args.base, args.head)
    if files is None:
        print("диапазон коммитов не разрешён — гоним всё", file=sys.stderr)
        _print({key: True for key in AREAS})
        return 0

    _print(
        {
            key: any(path.startswith(prefixes) for path in files)
            for key, prefixes in AREAS.items()
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Шаг 4: Убедиться, что тесты зелёные**

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/unit/test_ci_changed_areas.py -q
```
Ожидается: `5 passed`.

- [ ] **Шаг 5: Проверить руками на реальном диапазоне**

```bash
python3 -B scripts/ci_changed_areas.py --base HEAD~5 --head HEAD
python3 -B scripts/ci_changed_areas.py --base 0000000000000000000000000000000000000000 --head HEAD
python3 -B scripts/ci_changed_areas.py --base deadbeefdeadbeefdeadbeefdeadbeefdeadbeef --head HEAD
```
Ожидается: первый — по факту изменений; второй и третий — обе области `true`
и пояснение в stderr.

- [ ] **Шаг 6: Линтер и коммит**

```bash
.venv/bin/ruff check scripts/ci_changed_areas.py tests/unit/test_ci_changed_areas.py
.venv/bin/ruff format --check scripts/ci_changed_areas.py tests/unit/test_ci_changed_areas.py
git add scripts/ci_changed_areas.py tests/unit/test_ci_changed_areas.py
git commit -m "feat(ci): детектор затронутых областей, сдающийся в пользу прогона

Пропуск проверки по ошибке дороже лишнего прогона, поэтому любая
неопределённость — первый пуш ветки, force-push, база из сорока нулей,
недостижимый коммит — разрешается в пользу «гоняем всё».

Областей ровно две: вход Storybook с Playwright и содержимое control bundle.
Больше в этом репозитории локализованных входов нет — джоба backend линтует
весь репозиторий и читает .github, fbctl, deploy и packages из своих тестов.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: `ui-evidence` не гоняет браузеры по нетронутому фронту

**Files:**
- Modify: `.github/workflows/verify.yml`
- Modify: `.github/workflows/release.yml`
- Modify: `tests/unit/test_platform_supply_chain.py`

**Interfaces:**
- Consumes: `scripts/ci_changed_areas.py` из задачи 1.
- Produces: вход `full_run` у `verify.yml`, джоба `changes` с выходом `ui`.

**Контекст.** `ui-evidence` — Storybook и Playwright, 4.5 минуты, 84% прогонов
тратят их зря. Её вход честно локализован: бэкенд она не поднимает, данные берёт
из рукописных фикстур (`frontend/e2e/operatorTestHarness.ts`).

**Осторожно:** гард версии Playwright (`test_ui_evidence_is_a_release_gate`)
читает `pnpm-lock.yaml` и живёт в джобе `backend` — она не фильтруется, так что
рассинхрон клиента и образа поймается в любом случае.

- [ ] **Шаг 1: Написать падающий гард**

Добавить в `tests/unit/test_platform_supply_chain.py`:

```python
def test_only_localised_checks_are_skippable_and_release_always_runs_all() -> None:
    """Фильтруется только то, чей вход локализован; релиз гоняет всё.

    Джоба backend линтует весь репозиторий и читает .github, fbctl, deploy,
    docker и packages из собственных тестов — её вход не локализован, и
    фильтровать её нельзя. Джоба frontend гоняет sync:api, который импортирует
    apps.api.main, — правка бэкенда её вход. Решение о пропуске принимается до
    вызова verify.yml и приходит входом: результат вызванного workflow не
    отличает «прошло всё» от «пропущено всё».
    """
    verify = VERIFY_WORKFLOW.read_text(encoding="utf-8")
    release = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    document = yaml.safe_load(verify)

    assert document["on"]["workflow_call"]["inputs"]["full_run"]["type"] == "boolean"
    assert document["on"]["workflow_call"]["inputs"]["full_run"]["default"] is False
    assert "full_run: ${{ github.event_name == 'workflow_dispatch' }}" in release

    for always_on in ("backend", "frontend", "platform"):
        assert "if" not in document["jobs"][always_on], (
            f"джоба {always_on} читает весь репозиторий и пропуску не подлежит"
        )

    evidence_if = " ".join(str(document["jobs"]["ui-evidence"]["if"]).split())
    assert evidence_if == (
        "${{ inputs.full_run || needs.changes.outputs.ui == 'true' }}"
    )
    assert document["jobs"]["ui-evidence"]["needs"] == ["changes"]
    assert "scripts/ci_changed_areas.py" in verify
```

- [ ] **Шаг 2: Убедиться, что гард красный**

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/unit/test_platform_supply_chain.py::test_only_localised_checks_are_skippable_and_release_always_runs_all -q
```
Ожидается: FAIL на отсутствии входа `full_run`.

- [ ] **Шаг 3: Объявить вход у `verify.yml`**

В `.github/workflows/verify.yml` заменить

```yaml
on:
  pull_request:
    branches: [main]
  workflow_call:
```

на

```yaml
on:
  pull_request:
    branches: [main]
  workflow_call:
    inputs:
      full_run:
        description: >-
          Гнать всё без учёта состава изменений. Релиз выставляет true:
          результат вызванного workflow не отличает «прошло всё» от
          «пропущено всё», поэтому решение принимается снаружи.
        required: false
        type: boolean
        default: false
```

- [ ] **Шаг 4: Передать вход из релиза**

В `.github/workflows/release.yml`, в джобе `verify`, добавить после `uses:`:

```yaml
    with:
      full_run: ${{ github.event_name == 'workflow_dispatch' }}
```

- [ ] **Шаг 5: Добавить джобу `changes`**

В `.github/workflows/verify.yml` первой джобой в `jobs:`:

```yaml
  changes:
    name: Affected areas
    runs-on: ubuntu-latest
    timeout-minutes: 5
    outputs:
      ui: ${{ steps.detect.outputs.ui }}
    steps:
      # Нужна история: детектор сравнивает два коммита, а checkout по умолчанию
      # приносит только head.
      - name: Checkout
        uses: actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5 # v4
        with:
          fetch-depth: 0

      # Пропуск по ошибке дороже лишнего прогона: неизвестная база печатает
      # все области как затронутые, и скрипт делает это сам.
      - name: Detect affected areas
        id: detect
        env:
          BASE: ${{ github.event_name == 'pull_request' && github.event.pull_request.base.sha || github.event.before }}
          HEAD: ${{ github.sha }}
        run: |
          if [ "${{ inputs.full_run }}" = "true" ]; then
            python3 -B scripts/ci_changed_areas.py --assume-all >> "$GITHUB_OUTPUT"
          else
            python3 -B scripts/ci_changed_areas.py --base "$BASE" --head "$HEAD" >> "$GITHUB_OUTPUT"
          fi
```

- [ ] **Шаг 6: Подключить условие к `ui-evidence`**

В той же джобе `ui-evidence` добавить сразу после `timeout-minutes: 30`:

```yaml
    needs: [changes]
    if: ${{ inputs.full_run || needs.changes.outputs.ui == 'true' }}
```

- [ ] **Шаг 7: Проверки**

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/unit/test_platform_supply_chain.py tests/unit/test_ci_timeouts.py tests/unit/test_ci_changed_areas.py -q
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -c "import yaml,pathlib; [yaml.safe_load(pathlib.Path(p).read_text(encoding='utf-8')) for p in ('.github/workflows/verify.yml','.github/workflows/release.yml')] and print('ok')"
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/unit -q --timeout=60
```
Ожидается: всё зелёное. Гард из задачи 1 обязан принять новую джобу `changes` —
у неё есть `timeout-minutes`.

- [ ] **Шаг 8: Коммит**

```bash
git add .github/workflows/verify.yml .github/workflows/release.yml tests/unit/test_platform_supply_chain.py
git commit -m "feat(ci): приёмка браузерами не идёт по нетронутому фронту

Storybook и Playwright занимают 4.5 минуты и гонялись на каждом прогоне, хотя
фронт трогали 4 коммита из последних 25. Вход этой джобы честно локализован:
бэкенд она не поднимает, данные берёт из рукописных фикстур.

Решение о пропуске принимается ДО вызова verify.yml и приходит входом
full_run: результат вызванного workflow не отличает «прошли все четыре джобы»
от «пропустили все четыре», и фильтрация внутри дала бы релизу зелёный verify
на коммите, который никто не проверял. Ручной релиз выставляет full_run=true и
гоняет всё.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: Шарды отказов репетиции идут по содержимому бандла

**Files:**
- Modify: `.github/workflows/release.yml`
- Modify: `tests/unit/test_platform_supply_chain.py`

**Контекст.** Репетиция — 10.5 минуты, больше половины прогона. Пять шардов:
четыре `failpoints` проверяют семантику отказов и очистки самого `fbctl`, их
настоящий вход — содержимое control bundle. Пятый, `acceptance`, — единственный
runtime-тест прикладного кода: миграции против живой БД, старт API с боевым
окружением, `/api/operator/snapshot` с проверкой `DataState` шести секций,
heartbeat всех одиннадцати воркеров, полный жизненный цикл инцидента в Telegram.
Локально его воспроизвести нечем.

**Поэтому `acceptance` идёт ВСЕГДА, а четыре `failpoints` — по области
`bundle`.** Правка в `core/` или `apps/` ломает именно `acceptance`, и он
остаётся.

Существующий гард `test_release_requires_real_single_slot_rehearsal_before_production`
утверждает `rehearsal.count("- case: ") == 5` и `max-parallel >= 5`. Матрица
становится динамической, поэтому гард переписывается — но требование «на релизе
шардов ровно пять» обязано сохраниться.

- [ ] **Шаг 1: Переписать гард**

В `tests/unit/test_platform_supply_chain.py` в
`test_release_requires_real_single_slot_rehearsal_before_production` заменить
блок проверок состава матрицы (строки с `rehearsal.count("- case: ")`,
`parallel_limit` и циклом `for index in range(4)`) на:

```python
    # Матрица стала динамической: на релизе — все пять шардов, на пуше — только
    # те, чей вход изменился. Состав формирует один шаг, и он же обязан
    # оставлять acceptance всегда: это единственный runtime-тест apps/ и core/,
    # локально не воспроизводимый.
    assert "matrix: ${{ fromJSON(needs.rehearsal-plan.outputs.matrix) }}" in rehearsal
    plan = _job_block(release, "rehearsal-plan")
    assert '"case": "acceptance"' in plan
    assert plan.count('"scenario": "failpoints"') == 4
    assert '"shard_count": 4' in plan
    assert "github.event_name == 'workflow_dispatch'" in plan
    assert "needs.verify.outputs.bundle" in plan
```

- [ ] **Шаг 2: Убедиться, что гард красный**

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/unit/test_platform_supply_chain.py::test_release_requires_real_single_slot_rehearsal_before_production -q
```
Ожидается: FAIL на отсутствии `rehearsal-plan`.

- [ ] **Шаг 3: Отдать область `bundle` наружу из `verify.yml`**

В `.github/workflows/verify.yml` в блок `on.workflow_call` добавить outputs:

```yaml
    outputs:
      bundle:
        description: Затронуто ли содержимое control bundle
        value: ${{ jobs.changes.outputs.bundle }}
```

и в джобе `changes` добавить второй выход:

```yaml
      bundle: ${{ steps.detect.outputs.bundle }}
```

- [ ] **Шаг 4: Добавить планировщик матрицы**

В `.github/workflows/release.yml` перед джобой `docker-rehearsal`:

```yaml
  rehearsal-plan:
    name: Plan rehearsal shards
    runs-on: ubuntu-latest
    timeout-minutes: 5
    if: ${{ !cancelled() && needs.images.result == 'success' && needs.control-bundle.result == 'success' }}
    needs: [verify, images, control-bundle]
    outputs:
      matrix: ${{ steps.plan.outputs.matrix }}
    steps:
      # acceptance идёт всегда: это единственный runtime-тест apps/ и core/ —
      # миграции против живой БД, старт API с боевым окружением, снимок
      # оператора и heartbeat одиннадцати воркеров. Локально не воспроизводится.
      # Четыре шарда failpoints проверяют семантику отказов самого fbctl, их
      # вход — содержимое control bundle.
      - name: Plan shards
        id: plan
        env:
          FULL: ${{ github.event_name == 'workflow_dispatch' }}
          BUNDLE: ${{ needs.verify.outputs.bundle }}
        run: |
          acceptance='{"case":"acceptance","scenario":"acceptance","shard_index":0,"shard_count":1}'
          failpoints=''
          if [ "$FULL" = "true" ] || [ "$BUNDLE" = "true" ]; then
            for index in 0 1 2 3; do
              failpoints="${failpoints},{\"case\":\"fp${index}\",\"scenario\":\"failpoints\",\"shard_index\":${index},\"shard_count\":4}"
            done
          fi
          printf 'matrix={"include":[%s%s]}\n' "$acceptance" "$failpoints" >> "$GITHUB_OUTPUT"
```

- [ ] **Шаг 5: Перевести матрицу репетиции на динамическую**

В джобе `docker-rehearsal` заменить `needs:` и весь блок `strategy:` на:

```yaml
    needs: [images, control-bundle, rehearsal-plan]
    strategy:
      # Провал одного шарда не должен прятать остальные — иначе один отчёт
      # вместо пяти, и следующая причина всплывает только на следующем прогоне.
      fail-fast: false
      max-parallel: 5
      matrix: ${{ fromJSON(needs.rehearsal-plan.outputs.matrix) }}
```

и в его `if:` добавить `&& needs.rehearsal-plan.result == 'success'`.

- [ ] **Шаг 6: Дописать планировщик в отчёт**

Гард из `test_release_reports_its_outcome_without_leaking_details` требует, чтобы
`needs` отчёта совпадали с составом workflow. Добавить `rehearsal-plan` в
`needs:` джобы `report`, в её `env:` — `PLAN: ${{ needs.rehearsal-plan.result }}`,
в цикл — пару `"план репетиции:$PLAN"`, а в
`_REPORT_ALLOWED_EXPRESSIONS` — `needs.rehearsal-plan.result`.

- [ ] **Шаг 7: Проверки**

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/unit -q --timeout=60
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -c "import yaml,pathlib; d=yaml.safe_load(pathlib.Path('.github/workflows/release.yml').read_text(encoding='utf-8')); print(sorted(d['jobs']))"
```
Ожидается: всё зелёное; в списке джоб есть `rehearsal-plan`.

Отдельно проверить, что планировщик печатает валидный JSON в обоих режимах:

```bash
FULL=true BUNDLE=false bash -c 'acceptance='"'"'{"case":"acceptance","scenario":"acceptance","shard_index":0,"shard_count":1}'"'"'; failpoints=""; if [ "$FULL" = "true" ] || [ "$BUNDLE" = "true" ]; then for index in 0 1 2 3; do failpoints="${failpoints},{\"case\":\"fp${index}\",\"scenario\":\"failpoints\",\"shard_index\":${index},\"shard_count\":4}"; done; fi; printf "{\"include\":[%s%s]}\n" "$acceptance" "$failpoints"' | python3 -c "import json,sys; d=json.load(sys.stdin); print(len(d['include']), 'шардов')"
```
Ожидается: `5 шардов`. Повторить с `FULL=false BUNDLE=false` — ожидается
`1 шардов`.

- [ ] **Шаг 8: Коммит**

```bash
git add .github/workflows/verify.yml .github/workflows/release.yml tests/unit/test_platform_supply_chain.py
git commit -m "feat(ci): шарды отказов репетиции идут по содержимому бандла

Репетиция занимает 10.5 минуты — больше половины прогона. Четыре шарда
failpoints проверяют семантику отказов и очистки самого fbctl, их вход —
содержимое control bundle, и на пуше без его правок они не нужны.

Шард acceptance идёт всегда: это единственный runtime-тест apps/ и core/ —
миграции против живой БД, старт API с боевым окружением, снимок оператора и
heartbeat одиннадцати воркеров. Локально его воспроизвести нечем, а правка
прикладного кода ломает именно его. Ручной релиз гоняет все пять шардов.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Условие выхода

Прогон от пуша с правкой только бэкенда не запускает `ui-evidence` и четыре
шарда `failpoints`, проходит примерно за 7 минут вместо 20, и это видно в
`gh run view`. Ручной релиз запускает всё: четыре джобы проверки и пять шардов
репетиции.

## Что план сознательно не делает

- Не фильтрует `backend`, `frontend` и `platform` — их вход не локализован.
- Не трогает `images`: контент-адресация уже даёт корректный пропуск, а отказ от
  прогонов остудил бы buildx-кэш и перенёс холодную сборку на релиз.
- Не переиспользует артефакты между прогонами: ретенция 7 дней, а «digest не
  менялся» не значит «этот digest проходил репетицию».
- Не добавляет ретраи: ретрай прячет причину, предел времени её обнажает.
