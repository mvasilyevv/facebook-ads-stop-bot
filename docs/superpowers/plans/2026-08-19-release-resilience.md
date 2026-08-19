# Живучесть релиза и язык оператора — план

> **Для агентов:** ОБЯЗАТЕЛЬНАЯ ПОД-СКИЛЛ: используйте superpowers:subagent-driven-development
> или superpowers:executing-plans, выполняя план задача за задачей. Шаги помечены
> чекбоксами (`- [ ]`).

**Цель:** релиз перестаёт зависать молча и умеет сказать о себе, а гарды говорят
обо всей релизной поверхности, а не о файле, где случился инцидент.

**Архитектура:** четыре волны, каждая снимает ограничение для следующей. Волны 1–2
описаны здесь пошагово. Волны 3–4 получают собственные планы: их размер
неизвестен до первого прогона гарда, а план без измерения выродится в заглушки.

**Технологии:** GitHub Actions, Python 3.12, pytest, PyYAML, fbctl (zipapp),
Telegram Bot API.

## Global Constraints

- Код, тесты и комментарии — по-русски; имена типов, API-полей и технических
  идентификаторов остаются английскими.
- Сначала падающий гард, потом правка. Гард обязан покраснеть до правки и
  позеленеть после — без этого задача не считается выполненной.
- Гард формулируется как утверждение обо **всей** релизной поверхности: три
  workflow (`verify.yml`, `release.yml`, `publish-images.yml`) плюс путь деплоя
  в `fbctl/`. Утверждение про один файл — причина, по которой 19.08.2026 один и
  тот же дефект чинили дважды.
- Сырой текст исключения, traceback, UUID, токен бота и секреты не попадают в
  operator UI, Telegram, URL, логи и breadcrumbs.
- Ветку не создавать: коммиты идут прямо в `main`, сообщение по-русски,
  `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- Коммитить только файлы своей задачи. В рабочем дереве лежат чужие изменения
  (`frontend/e2e/__audit__/1440px/*.png` от параллельной сессии) — не трогать,
  не коммитить, `git stash` не выполнять.
- `pytest` запускать только на изолированной БД. Боевая база `:5433` под
  запретом: integration-фикстуры сносят `offers` и `offer_rules`.
- Ничего не создавать, не публиковать и не менять внутри боевых рекламных
  кабинетов. Сканирование не включать.
- Перед push: `git fetch origin` и сверка с `origin/main`.

---

### Task 1: Предел времени объявляет каждая джоба

**Files:**
- Modify: `tests/unit/test_ci_timeouts.py`
- Modify: `.github/workflows/release.yml`
- Modify: `.github/workflows/publish-images.yml`

**Interfaces:**
- Consumes: ничего от других задач.
- Produces: `_steps_jobs(path) -> dict[str, dict]` в `tests/unit/test_ci_timeouts.py` —
  джобы, которым GitHub разрешает `timeout-minutes`. Задача 2 её не использует.

**Контекст.** 19.08.2026 джоба `platform` зависла на `apt-get` и её срубил
предел времени — предел работал только потому, что был объявлен. Из 15 джоб
предел есть у 5. Дефолт GitHub — 360 минут, то есть зависший `deploy` оставит
production остановленным на шесть часов молча.

**Важно:** джоба, вызывающая reusable workflow (`uses:` вместо `steps:`),
`timeout-minutes` не принимает — GitHub это запрещает. Таких две: `verify` и
`images` в `release.yml`. Предел за них объявляют джобы внутри вызванного
workflow. Гард обязан их пропускать, иначе станет невыполнимым.

- [ ] **Шаг 1: Переписать гард на все три workflow**

Заменить содержимое `tests/unit/test_ci_timeouts.py` целиком на:

```python
# -*- coding: utf-8 -*-
"""У каждой джобы релизной поверхности есть свой предел времени.

18.08.2026 джоба platform зависла на `apt-get install` и шла 29 минут — её
остановил человек, а не пайплайн. 19.08.2026 предел уже стоял и срубил тот же
затык за десять минут, но проверял этот гард только verify.yml: предел был у 5
джоб из 15. Дефолт GitHub — 360 минут, то есть зависший deploy оставит
production остановленным на шесть часов молча.
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
_WORKFLOWS = (
    ROOT / ".github/workflows/verify.yml",
    ROOT / ".github/workflows/release.yml",
    ROOT / ".github/workflows/publish-images.yml",
)
_VERIFY = ROOT / ".github/workflows/verify.yml"

# Потолок ловит зависание, но не рубит честный прогон. У проверки он жёстче:
# самая медленная её джоба идёт пять минут, а сборка образов на холодном кэше —
# десятки.
_MAX_TIMEOUT_MINUTES = 60
_VERIFY_MAX_TIMEOUT_MINUTES = 40


def _steps_jobs(path: Path) -> dict[str, dict]:
    """Джобы, которым GitHub разрешает timeout-minutes.

    Джоба-вызов reusable workflow (`uses:`) предел времени не принимает: его
    объявляют джобы внутри вызванного workflow.
    """
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    return {name: job for name, job in document["jobs"].items() if "uses" not in job}


def test_every_job_of_the_release_surface_declares_a_timeout() -> None:
    missing: list[str] = []
    for path in _WORKFLOWS:
        for name, job in _steps_jobs(path).items():
            if "timeout-minutes" not in job:
                missing.append(f"{path.name}:{name}")
    assert missing == [], (
        "джобы без timeout-minutes: "
        + ", ".join(missing)
        + " — без предела зависший шаг идёт до лимита раннера в 360 минут"
    )


def test_timeouts_stay_tight() -> None:
    too_long = {
        f"{path.name}:{name}": job["timeout-minutes"]
        for path in _WORKFLOWS
        for name, job in _steps_jobs(path).items()
        if int(job.get("timeout-minutes", 0)) > _MAX_TIMEOUT_MINUTES
    }
    assert too_long == {}, f"предел выше {_MAX_TIMEOUT_MINUTES} минут не ловит зависание: {too_long}"


def test_verification_timeouts_stay_tighter_than_image_builds() -> None:
    too_long = {
        name: job["timeout-minutes"]
        for name, job in _steps_jobs(_VERIFY).items()
        if int(job.get("timeout-minutes", 0)) > _VERIFY_MAX_TIMEOUT_MINUTES
    }
    assert too_long == {}, (
        f"проверка не строит образы и не должна занимать больше "
        f"{_VERIFY_MAX_TIMEOUT_MINUTES} минут: {too_long}"
    )
```

- [ ] **Шаг 2: Убедиться, что гард красный**

Выполнить:
```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/unit/test_ci_timeouts.py -q
```
Ожидается: FAIL с перечислением восьми джоб —
`release.yml:bootstrap-source-preflight`, `release.yml:control-bundle`,
`release.yml:deploy`, `publish-images.yml:plan`, `publish-images.yml:base`,
`publish-images.yml:app`, `publish-images.yml:desktop`,
`publish-images.yml:manifest`. Джоб `release.yml:verify` и `release.yml:images`
в списке быть НЕ должно.

- [ ] **Шаг 3: Объявить предел у восьми джоб**

В `.github/workflows/release.yml` добавить строку `timeout-minutes:` сразу
после `runs-on:` соответствующей джобы:

- `bootstrap-source-preflight` → `timeout-minutes: 20`
- `control-bundle` → `timeout-minutes: 20`
- `deploy` → `timeout-minutes: 40`

В `.github/workflows/publish-images.yml` — так же:

- `plan` → `timeout-minutes: 15`
- `base` → `timeout-minutes: 45`
- `app` → `timeout-minutes: 60`
- `desktop` → `timeout-minutes: 60`
- `manifest` → `timeout-minutes: 15`

Значения выбраны так: наблюдённое время `deploy` — 2 минуты, его два внутренних
шага уже ограничены 25 минутами каждый, из них выполняется один; сборкам образов
дан запас на холодный кэш, потому что в наблюдённых прогонах они переиспользовали
digest и шли меньше минуты.

- [ ] **Шаг 4: Убедиться, что гард зелёный**

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/unit/test_ci_timeouts.py -q
```
Ожидается: `3 passed`.

- [ ] **Шаг 5: Проверить синтаксис workflow**

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -c "import yaml,pathlib; [yaml.safe_load(pathlib.Path(p).read_text(encoding='utf-8')) for p in ('.github/workflows/release.yml','.github/workflows/publish-images.yml')] and print('ok')"
```
Ожидается: `ok`.

- [ ] **Шаг 6: Коммит**

```bash
git add tests/unit/test_ci_timeouts.py .github/workflows/release.yml .github/workflows/publish-images.yml
git commit -m "fix(ci): предел времени объявляет каждая джоба релиза

Гард на таймауты читал только verify.yml — предел был у 5 джоб из 15.
Зависший deploy при дефолте GitHub оставил бы production остановленным на
шесть часов молча. Теперь утверждение накрывает три workflow; джобы-вызовы
reusable workflow пропускаются, им GitHub timeout-minutes не разрешает.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Закрепление внешних зависимостей утверждено гардом

**Files:**
- Modify: `tests/unit/test_platform_supply_chain.py`

**Interfaces:**
- Consumes: `VERIFY_WORKFLOW`, `IMAGE_WORKFLOW`, `RELEASE_WORKFLOW` — уже
  объявлены в начале файла.
- Produces: ничего для других задач.

**Контекст.** Сейчас все внешние `uses:` закреплены по SHA, а все `image:` — по
digest. Это правда, но не утверждено ничем: `test_external_docker_bases_are_digest_pinned`
проверяет только `Dockerfile`, а закрепление образа Playwright стережёт частный
гард под конкретный образ. Один тег вместо digest возвращает изменяемую внешнюю
зависимость, а вместе с ней — молчаливый дрейф проверки.

- [ ] **Шаг 1: Написать падающий гард**

Добавить в `tests/unit/test_platform_supply_chain.py` сразу после
`test_ci_installs_nothing_from_ubuntu_package_mirrors`:

```python
def test_every_external_workflow_dependency_is_immutable() -> None:
    """Внешнее в CI закреплено неизменяемо: action — по SHA, образ — по digest.

    Тег переезжает без нашего ведома, и проверка начинает мерить не тот код,
    который мы читаем. Локальные `uses: ./…` — наш же файл в этом коммите,
    им закрепление не нужно.
    """
    action_sha = re.compile(r"@[0-9a-f]{40}$")

    for workflow in (VERIFY_WORKFLOW, IMAGE_WORKFLOW, RELEASE_WORKFLOW):
        source = workflow.read_text(encoding="utf-8")

        for match in re.finditer(r"(?m)^\s*uses:\s*(\S+)", source):
            ref = match.group(1)
            if ref.startswith("./"):
                continue
            assert action_sha.search(ref), f"{workflow.name}: action не закреплён по SHA — {ref}"

        for match in re.finditer(r"(?m)^\s*image:\s*(\S+)", source):
            ref = match.group(1)
            assert "@sha256:" in ref, f"{workflow.name}: образ не закреплён по digest — {ref}"

        # `docker run` тянет образ мимо ключа image: и обязан быть закреплён так же.
        for block in re.finditer(r"docker run(?P<body>(?:.|\n)*?)(?=\n\s*- name:|\n\n|\Z)", source):
            assert "@sha256:" in block.group("body"), (
                f"{workflow.name}: docker run без digest — образ может переехать между прогонами"
            )
```

- [ ] **Шаг 2: Убедиться, что гард проходит на текущем коде**

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/unit/test_platform_supply_chain.py::test_every_external_workflow_dependency_is_immutable -q
```
Ожидается: `1 passed`. Гард фиксирует уже достигнутое состояние — правки кода
он не требует.

- [ ] **Шаг 3: Доказать, что гард умеет краснеть**

Временно заменить в `.github/workflows/verify.yml` строку
`uses: actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5 # v4`
на `uses: actions/checkout@v4` (первое вхождение), выполнить команду из шага 2 и
убедиться, что тест ПАДАЕТ с текстом `action не закреплён по SHA`. Затем вернуть
строку обратно и убедиться, что тест снова проходит.

Это обязательный шаг: гард, который никогда не краснел, не доказан.

- [ ] **Шаг 4: Проверить, что рабочее дерево вернулось в исходное состояние**

```bash
git diff --stat .github/workflows/verify.yml
```
Ожидается: пустой вывод.

- [ ] **Шаг 5: Полный файл гардов**

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/unit/test_platform_supply_chain.py -q
```
Ожидается: все тесты проходят.

- [ ] **Шаг 6: Коммит**

```bash
git add tests/unit/test_platform_supply_chain.py
git commit -m "test(ci): закрепление внешних зависимостей стало утверждением

Все actions в CI закреплены по SHA, все образы — по digest, но держалось это
ни на чём: существующий гард проверял только Dockerfile, а закрепление образа
Playwright стерёг частный тест под конкретный образ. Тег переезжает без нашего
ведома, и проверка начинает мерить не тот код, который мы читаем.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: SSH на production не виснет

**Files:**
- Modify: `.github/workflows/release.yml`
- Modify: `tests/unit/test_platform_supply_chain.py`

**Interfaces:**
- Consumes: `_job_block(text, job)` из `tests/unit/test_platform_supply_chain.py`.
- Produces: ничего для других задач.

**Контекст.** Ключ `~/.ssh/config` собирается в двух местах `release.yml` —
в джобе `bootstrap-source-preflight` (около строки 48) и в джобе `deploy`
(около строки 252). Ни одно не задаёт `ConnectTimeout` и `ServerAliveInterval`:
если хост перестаёт отвечать посреди сессии, `ssh` ждёт бесконечно. Шаги
`Install temporary GHCR credentials on production` и `Remove temporary GHCR
credentials` собственного предела времени не имеют; у второго есть `if: always()`,
так что он не пропускается, но зависнуть может.

- [ ] **Шаг 1: Написать падающий гард**

Добавить в `tests/unit/test_platform_supply_chain.py`:

```python
def test_production_ssh_cannot_hang_forever() -> None:
    """SSH на прод обязан сдаваться сам.

    Без ConnectTimeout и ServerAliveInterval умерший посреди сессии хост держит
    шаг до предела джобы, а шаги вокруг деплоя работают с временными кредами
    GHCR на production — их снятие ждать не должно.
    """
    release = RELEASE_WORKFLOW.read_text(encoding="utf-8")

    assert release.count("'  ConnectTimeout 15'") == 2, (
        "ConnectTimeout задан не во всех двух местах, где собирается ~/.ssh/config"
    )
    assert release.count("'  ServerAliveInterval 15'") == 2
    assert release.count("'  ServerAliveCountMax 4'") == 2

    for step in ("Install temporary GHCR credentials on production", "Remove temporary GHCR credentials"):
        body = release.split(f"- name: {step}", maxsplit=1)[1].split("- name:", maxsplit=1)[0]
        assert "timeout-minutes: 5" in body, f"шаг «{step}» без собственного предела времени"
```

- [ ] **Шаг 2: Убедиться, что гард красный**

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/unit/test_platform_supply_chain.py::test_production_ssh_cannot_hang_forever -q
```
Ожидается: FAIL на первом же `assert` (`ConnectTimeout задан не во всех…`).

- [ ] **Шаг 3: Закрепить настройки SSH**

В `.github/workflows/release.yml` — в **обоих** местах, где собирается
`~/.ssh/config`, заменить блок

```yaml
          printf '%s\n' \
            'Host *' \
            '  StrictHostKeyChecking yes' \
            '  UserKnownHostsFile ~/.ssh/known_hosts' \
            '  IdentitiesOnly yes' > ~/.ssh/config
```

на

```yaml
          printf '%s\n' \
            'Host *' \
            '  StrictHostKeyChecking yes' \
            '  UserKnownHostsFile ~/.ssh/known_hosts' \
            '  IdentitiesOnly yes' \
            '  ConnectTimeout 15' \
            '  ServerAliveInterval 15' \
            '  ServerAliveCountMax 4' > ~/.ssh/config
```

- [ ] **Шаг 4: Дать предел шагам с кредами**

Добавить `timeout-minutes: 5` в шаги `Install temporary GHCR credentials on
production` и `Remove temporary GHCR credentials` — строкой сразу после
`- name:` соответствующего шага, до `env:`.

- [ ] **Шаг 5: Убедиться, что гард зелёный, и проверить синтаксис**

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/unit/test_platform_supply_chain.py tests/unit/test_ci_timeouts.py -q
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -c "import yaml,pathlib; yaml.safe_load(pathlib.Path('.github/workflows/release.yml').read_text(encoding='utf-8')) and print('ok')"
```
Ожидается: все тесты проходят, затем `ok`.

- [ ] **Шаг 6: Коммит**

```bash
git add .github/workflows/release.yml tests/unit/test_platform_supply_chain.py
git commit -m "fix(ci): SSH на production сдаётся сам, а не висит

Ключ ~/.ssh/config собирается в двух джобах release.yml и ни в одной не задавал
ConnectTimeout и ServerAliveInterval: умерший посреди сессии хост держал бы шаг
до предела джобы. Шаги, которые кладут и снимают временные креды GHCR на
production, получили собственный предел в пять минут.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: Предикат ожидания только читает

**Files:**
- Create: `tests/unit/test_fbctl_wait_predicates.py`
- Modify: `fbctl/probes.py` (только docstring `wait_for`)

**Interfaces:**
- Consumes: `wait_for(description, check, *, timeout, interval, monotonic, sleep)`
  из `fbctl/probes.py`.
- Produces: ничего для других задач.

**Контекст.** 19.08.2026 деплой падал на шаге `ensure_desktop_channel`. Причина:
внутрь `wait_for` был передан `ensure_browser_channel`, который не наблюдает
состояние, а **перезапускает профиль Vision**. Опрос раз в пять секунд означал
36 принудительных перезапусков за 180 секунд, и холодный старт, идущий дольше
интервала опроса, не мог завершиться ни при каком пределе времени. Правка
развела лечение и наблюдение: лечим один раз, дальше ждём чтением.

Сейчас все пять вызовов `wait_for` в `fbctl/controller.py` передают предикаты
`require_*` и `_check_*` — то есть инвариант выполняется, но не утверждён.

- [ ] **Шаг 1: Написать гард**

Создать `tests/unit/test_fbctl_wait_predicates.py`:

```python
# -*- coding: utf-8 -*-
"""Ожидание опрашивает состояние, а не чинит его.

19.08.2026 деплой не мог пройти шаг канала стола. Внутрь wait_for был передан
ensure_browser_channel — ручка, которая перезапускает профиль Vision. Опрос раз
в пять секунд давал 36 принудительных перезапусков за 180 секунд, и холодный
старт, идущий дольше интервала опроса, не завершался ни при каком пределе
времени. Лечение — действие, его выполняют один раз; ожидание после этого
только читает.

Гард держит соглашение об именах: предикат wait_for обязан называться
require_* или _check_*, и такие функции не выполняют действий.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_FBCTL = ROOT / "fbctl"

_ALLOWED_PREFIXES = ("require_", "_check_")


def _wait_for_predicates() -> list[tuple[str, str]]:
    """Пары (файл, имя вызываемого) для каждого предиката, переданного в wait_for."""
    found: list[tuple[str, str]] = []
    for source_file in sorted(_FBCTL.rglob("*.py")):
        tree = ast.parse(source_file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            callee = node.func
            if not (isinstance(callee, ast.Name) and callee.id == "wait_for"):
                continue
            if len(node.args) < 2:
                continue
            predicate = node.args[1]
            if isinstance(predicate, ast.Lambda):
                body = predicate.body
                if isinstance(body, ast.Call):
                    target = body.func
                    name = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", "")
                    found.append((source_file.name, name))
                    continue
                found.append((source_file.name, ast.dump(body)[:40]))
                continue
            name = predicate.attr if isinstance(predicate, ast.Attribute) else getattr(predicate, "id", "")
            found.append((source_file.name, name))
    return found


def test_every_wait_predicate_is_read_only() -> None:
    predicates = _wait_for_predicates()
    assert predicates, "не найдено ни одного вызова wait_for — гард смотрит не туда"

    offenders = [
        f"{where}:{name}"
        for where, name in predicates
        if not name.startswith(_ALLOWED_PREFIXES)
    ]
    assert offenders == [], (
        "предикат ожидания обязан только читать (require_* или _check_*): "
        + ", ".join(offenders)
        + " — лечащая ручка на месте пробы повторяет лечение каждый интервал опроса"
    )
```

- [ ] **Шаг 2: Убедиться, что гард зелёный на текущем коде**

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/unit/test_fbctl_wait_predicates.py -q
```
Ожидается: `1 passed`.

- [ ] **Шаг 3: Доказать, что гард умеет краснеть**

Временно заменить в `fbctl/controller.py` (около строки 996) строку
`lambda: require_exact_browser(self.probes, api, config.api_key),`
на `lambda: ensure_browser_channel(self.probes, api, config.api_key),`,
выполнить команду из шага 2 и убедиться, что тест ПАДАЕТ с упоминанием
`controller.py:ensure_browser_channel`. Затем вернуть строку обратно и убедиться,
что тест снова проходит и `git diff --stat fbctl/controller.py` пуст.

- [ ] **Шаг 4: Записать соглашение в docstring `wait_for`**

В `fbctl/probes.py` у функции `wait_for` (объявление около строки 130) добавить
docstring первой строкой тела:

```python
    """Ждать выполнения условия, ничего не меняя.

    `check` обязан только наблюдать состояние: его зовут каждый интервал опроса,
    и действие внутри повторится столько же раз. Лечение выполняется отдельным
    вызовом до ожидания. Соглашение об именах (require_* / _check_*) держит
    tests/unit/test_fbctl_wait_predicates.py.
    """
```

- [ ] **Шаг 5: Проверка**

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/unit/test_fbctl_wait_predicates.py tests/unit/test_fbctl.py -q
.venv/bin/ruff check fbctl tests/unit/test_fbctl_wait_predicates.py
.venv/bin/ruff format --check fbctl tests/unit/test_fbctl_wait_predicates.py
```
Ожидается: все тесты проходят, `ruff` чист.

- [ ] **Шаг 6: Коммит**

```bash
git add tests/unit/test_fbctl_wait_predicates.py fbctl/probes.py
git commit -m "test(fbctl): предикат ожидания обязан только читать

19.08 деплой не мог пройти шаг канала стола: внутрь wait_for была передана
ручка, которая перезапускает профиль Vision, и опрос раз в пять секунд давал
36 принудительных перезапусков за 180 секунд. Правка развела лечение и
наблюдение, но инвариант держался только на внимательности — теперь его
утверждает гард и повторяет docstring wait_for.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: Релиз сообщает о себе в Telegram

**Files:**
- Modify: `.github/workflows/release.yml`
- Modify: `tests/unit/test_platform_supply_chain.py`

**Interfaces:**
- Consumes: `_job_block(text, job)` из `tests/unit/test_platform_supply_chain.py`.
- Produces: ничего для других задач.

**Контекст.** 19.08.2026 три деплоя подряд не дошли до выкатки, и узнавал об
этом человек, открывая GitHub. Сообщение шлёт сам CI напрямую в Bot API:
единственный случай, ради которого это делается, — «приложение не поднялось»,
и в этот момент outbox приложения недоступен по определению. Это не отменяет
правило «бизнес-код не зовёт Bot API напрямую»: CI бизнес-кодом не является и
в базу не пишет.

**Секреты.** Шаги читают `secrets.TELEGRAM_BOT_TOKEN` и
`secrets.TELEGRAM_ALERT_CHAT_ID`. Пока владелец их не добавил, шаги обязаны
тихо пропускаться, а не рушить релиз.

**Что попадает в сообщение:** только исход, имя провалившейся джобы и ссылка на
прогон. Сырой текст ошибки, путь на хосте, содержимое лога и любые секреты —
никогда.

> **Правка по итогам ревью волны 1.** Черновик джобы ниже приравнивал `skipped`
> к успеху и не держал `bootstrap-source-preflight` в `needs`, из-за чего обычный
> push в `main` слал «Релиз выкачен», хотя деплой требует `workflow_dispatch`.
> Итоговая форма живёт в `.github/workflows/release.yml` и отличается от
> черновика: исход трёхзначный (`не выкачен` / `выкачен` / `собран, выкатка не
> запускалась`), `always()` заменён на `!cancelled()`, у шага есть
> `continue-on-error` и ретрай `curl`. Гард в
> `tests/unit/test_platform_supply_chain.py` утверждает именно итоговую форму.

- [ ] **Шаг 1: Написать падающий гард**

Добавить в `tests/unit/test_platform_supply_chain.py`:

```python
def test_release_reports_its_outcome_without_leaking_details() -> None:
    """О провале деплоя узнаёт Telegram, а не человек, открывший GitHub.

    Сообщение шлёт сам CI: случай, ради которого всё делается, — «приложение не
    поднялось», и outbox в этот момент недоступен. В текст идут только исход,
    имя джобы и ссылка на прогон.
    """
    release = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    report = _job_block(release, "report")

    assert "if: always()" in report
    assert "needs: [verify, images, control-bundle, docker-rehearsal, deploy]" in report
    assert "timeout-minutes: 5" in report
    # Пустой секрет не роняет релиз: пока владелец не завёл токен, шаг молчит.
    assert 'test -n "$TELEGRAM_BOT_TOKEN"' in report
    assert 'test -n "$TELEGRAM_CHAT_ID"' in report

    assert "api.telegram.org/bot" in report
    assert "--max-time 20" in report

    # Наружу уходит исход, имя джобы и ссылка — и ничего больше.
    assert "github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}" in report
    for leaked in ("toJSON(", "steps.", "::error", "$GITHUB_STEP_SUMMARY"):
        assert leaked not in report, f"в сообщение утекает лишнее: {leaked}"
```

- [ ] **Шаг 2: Убедиться, что гард красный**

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/unit/test_platform_supply_chain.py::test_release_reports_its_outcome_without_leaking_details -q
```
Ожидается: FAIL с `AssertionError: missing workflow job report`.

- [ ] **Шаг 3: Добавить джобу отчёта**

В `.github/workflows/release.yml` добавить последней джобой (на том же уровне
отступа, что и `deploy`):

```yaml
  report:
    name: Report release outcome
    runs-on: ubuntu-latest
    timeout-minutes: 5
    if: always()
    needs: [verify, images, control-bundle, docker-rehearsal, deploy]
    steps:
      # Наружу уходит только исход, имя провалившейся джобы и ссылка на прогон.
      # Сырой текст ошибки, пути на хосте и содержимое лога — никогда: сообщение
      # уходит в Telegram, а он хранит его дольше, чем живёт инцидент.
      - name: Notify Telegram
        env:
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_ALERT_CHAT_ID }}
          VERIFY: ${{ needs.verify.result }}
          IMAGES: ${{ needs.images.result }}
          BUNDLE: ${{ needs.control-bundle.result }}
          REHEARSAL: ${{ needs.docker-rehearsal.result }}
          DEPLOY: ${{ needs.deploy.result }}
          RUN_URL: ${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}
        run: |
          # Пока владелец не завёл секреты, релиз молчит, но не падает.
          test -n "$TELEGRAM_BOT_TOKEN" || { echo "токен не задан — отчёт пропущен"; exit 0; }
          test -n "$TELEGRAM_CHAT_ID" || { echo "чат не задан — отчёт пропущен"; exit 0; }

          failed=""
          for pair in "проверка:$VERIFY" "образы:$IMAGES" "бандл:$BUNDLE" "репетиция:$REHEARSAL" "выкатка:$DEPLOY"; do
            name="${pair%%:*}"
            result="${pair##*:}"
            case "$result" in
              success|skipped) ;;
              *) failed="${failed:+$failed, }$name ($result)" ;;
            esac
          done

          if [ -z "$failed" ]; then
            text="Релиз выкачен. ${RUN_URL}"
          else
            text="Релиз не выкачен. Встало на: ${failed}. ${RUN_URL}"
          fi

          # Токен в пути — требование Bot API, обойти его нечем. Наружу он не
          # утекает: GitHub маскирует значения секретов в логах, curl идёт с
          # --silent, а тело ответа отправляется в /dev/null.
          curl --silent --show-error --fail --max-time 20 \
            --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
            --data-urlencode "text=${text}" \
            "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" > /dev/null
```

- [ ] **Шаг 4: Убедиться, что гард зелёный и синтаксис цел**

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/unit/test_platform_supply_chain.py tests/unit/test_ci_timeouts.py -q
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -c "import yaml,pathlib; d=yaml.safe_load(pathlib.Path('.github/workflows/release.yml').read_text(encoding='utf-8')); print(sorted(d['jobs']))"
```
Ожидается: все тесты проходят; в списке джоб присутствует `report`.

- [ ] **Шаг 5: Убедиться, что новая джоба не осталась без предела**

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/unit/test_ci_timeouts.py -q
```
Ожидается: `3 passed` — гард из задачи 1 обязан принять `report`.

- [ ] **Шаг 6: Коммит**

```bash
git add .github/workflows/release.yml tests/unit/test_platform_supply_chain.py
git commit -m "feat(ci): релиз сам сообщает о своём исходе в Telegram

19.08 три деплоя подряд не дошли до выкатки, и узнавал об этом человек,
открывая GitHub. Отчёт шлёт сам CI напрямую в Bot API: случай, ради которого
всё делается, — «приложение не поднялось», и outbox приложения в этот момент
недоступен по определению. В сообщение идут только исход, имя провалившейся
джобы и ссылка на прогон. Без заведённых секретов шаг молчит, но не рушит
релиз.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Волны 3 и 4 — отдельными планами

Пошагово здесь не расписаны сознательно: их размер неизвестен до измерения, а
план, написанный без него, выродится в заглушки.

**Волна 3 — гард на утечку секретов.** Условие входа: волна 1 завершена.
Первая задача — измерительная: перенести `tests/unit/test_secret_leak_guards.py`
из ветки `feat/no-secret-leaks` на текущий `main`, прогнать и записать список
того, что он красит. Только после этого пишется план починки. В PR это было
100 файлов, но часть с тех пор переписана, и опираться на старое число нельзя.

**Волна 4 — `operator-language`.** Условие входа: волна 3 завершена. План уже
написан: `docs/superpowers/plans/2026-08-18-operator-language.md`, пять задач.
Порядок именно такой, потому что волна 3 запрещает сырые ошибки и UUID в текстах
для человека, а волна 4 эти тексты переписывает: обратный порядок означает
переписывать дважды.

## Что требуется от владельца

- Завести `TELEGRAM_BOT_TOKEN` и `TELEGRAM_ALERT_CHAT_ID` именно **репозиторными**
  секретами: Settings → Secrets and variables → Actions → Repository secrets.
  У джобы `report` нет `environment:`, поэтому секрет, положенный в окружение
  `production` (как все остальные секреты этого workflow), ей не виден — шаг
  тихо вышел бы с кодом 0, и отчёт не заработал бы никогда. Пока секретов нет,
  джоба печатает `::warning::` и не роняет релиз.
- Решить судьбу PR #127 (перекрыт коммитом `9f94964f`, предлагается закрыть).
- Три вопроса из описания PR #101, которые кодом не закрываются: постбек
  AdSet.pro идёт GET с токеном в query; одноразовый ticket стола остаётся в
  cross-origin launch URL; navigation capability Telegram сидит в `?nav=`.

## Чего план сознательно не делает

- Не трогает сканирование — решение владельца.
- Не меняет топологию релиза: single-slot, отсутствие runtime-отката и HA
  остаются как есть.
- Не добавляет ретраи там, где нужен предел времени: ретрай прячет причину,
  предел её обнажает.
