# Самовосстановление канала стола и зачистка следов веб-канала — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Деплой сам поднимает браузерный канал Vision после пересоздания стола, а документация и лицензии перестают описывать демонтированный веб-канал.

**Architecture:** Эндпоинт `POST /api/vision/ensure-cdp` уже умеет чинить канал, но требует, чтобы вызывающий уже владел эксклюзивным maintenance-фенсом, — поэтому его никто не вызывает. Учим его брать фенс самому (как это делает `/vision/reconnect`), после чего `fbctl` вызывает его отдельным шагом деплоя между `start_application` и `verify_application`. Остальные задачи убирают расхождения между реальностью и текстами: документация, third-party notices, подсказка оператору про приватную сеть.

**Tech Stack:** Python 3.12 / FastAPI / SQLAlchemy async (backend), `fbctl` (чистый stdlib, без зависимостей), pytest, React + Vitest (оба фронта).

## Global Constraints

- Код, тесты и комментарии — по-русски там, где это помогает оператору; имена типов, API-полей и технических идентификаторов — английские.
- Один архитектурный слой или один вертикальный slice за PR.
- Сначала падающий тест, потом реализация. Коммиты частые, по завершении каждой задачи.
- Backend-гейты: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/unit -q`, `.venv/bin/python -m ruff check .`, `.venv/bin/python -m ruff format --check .`.
- Если менялся HTTP-контракт: `PYTHON=.venv/bin/python pnpm run sync:api`, затем `git diff --exit-code -- frontend/openapi.json packages/shared/src/api/generated.ts` должен быть пустым (тот же гейт гоняет CI).
- Секреты не логируются и не попадают в ответы API, в UI и в сообщения об ошибках.
- `fbctl` не имеет внешних зависимостей: только stdlib.
- Прод-хост: `root@62.60.150.133`. Прод-релиз запускается только вручную: `gh workflow run release.yml --ref main`. Push в main НИЧЕГО не выкатывает.

---

## Контекст: что уже сделано (не переделывать)

KasmVNC снят полностью, канал к столу — нативный RustDesk через собственный брокер (`rustdesk-id`/`rustdesk-relay` в desktop-проекте). Стол публикует `rustdesk.json` в readiness-каталог, API отдаёт его владельцу через `GET /api/desktop/native`, оба фронта показывают ID/адрес/ключ. Гейт `verify_system_ready` больше не падает из-за выключенного владельцем сканирования. Релиз `31941650576` прошёл зелёным, прод промоутнут, воркеры подняты, снятый сайт `desktop.adpulse.su.caddy` удалён.

**Единственная незакрытая механика:** `docker compose up -d` на шаге `start_desktop` пересоздаёт контейнер `vision-webtop` каждым деплоем. Vision после этого поднимается без запущенного профиля (или с профилем без CDP-порта), канал остаётся `DEGRADED`, и `verify_application` падает с `live Vision profile does not match canonical configuration`. Сейчас это лечит временный host-скрипт `/root/vision-channel-heal.sh`, который надо запускать руками перед каждым релизом. Задачи 1–2 переносят это в платформу, задача 5 убирает скрипт.

---

## File Structure

| Файл | Ответственность | Задача |
|---|---|---|
| `apps/api/routers/v1/settings_vision.py` | `ensure-cdp` берёт эксклюзивный фенс сам, если владелец не передан | 1 |
| `tests/unit/test_vision_ensure_cdp.py` | Контракт `ensure-cdp` для обоих режимов владения | 1 |
| `fbctl/probes.py` | `post_json` в клиенте проб + проба `ensure_browser_channel` | 2 |
| `fbctl/controller.py` | Шаг деплоя `ensure_desktop_channel` + регистрация failpoint | 2 |
| `tests/unit/test_fbctl.py` | Тесты пробы и порядка шагов | 2 |
| `DEPLOYMENT.md`, `CLAUDE.md` | Описание desktop-проекта и портов Caddy без KasmVNC | 3 |
| `deploy/vision-webtop/THIRD_PARTY_NOTICES.md` | Лицензии ровно того, что реально в образе | 3 |
| `tests/unit/test_vision_webtop.py` | Гард: notices не описывают снятый софт | 3 |
| `frontend/src/routes/remote-desktop/index.tsx` | Подсказка оператору про приватную сеть | 4 |
| `frontend-mini/src/routes/desktop/index.tsx` | То же для мини-аппа | 4 |
| `frontend/src/tests/pages/RemoteDesktop.test.tsx`, `frontend-mini/src/tests/Desktop.test.tsx` | Тесты подсказки | 4 |

---

### Task 1: `ensure-cdp` сам берёт эксклюзивное обслуживание

**Files:**
- Modify: `apps/api/routers/v1/settings_vision.py:728-823`
- Test: `tests/unit/test_vision_ensure_cdp.py`

**Interfaces:**
- Consumes: `BrowserExclusiveMaintenance(engine, operation_kind=...)` из `core.tasks.browser_fence` — асинхронный контекст-менеджер, сам генерирует `owner` (32 hex) и захватывает фенс; уже используется в `post_vision_reconnect`. `BrowserMaintenanceGuard(engine, owner)` — адаптирует УЖЕ активного владельца.
- Produces: `POST /api/vision/ensure-cdp` без заголовка `X-FB-Agent-Browser-Maintenance-Owner` возвращает `VisionEnsureCdpResponse` с реальным результатом (`READY` / `RECOVERED` / `UNAVAILABLE`) вместо отказа. Схема ответа не меняется: `ok: bool`, `status: str`, `action: str`, `message: str`.

**Почему так:** эндпоинт задуман «для platform desktop healer» (так написано в докстринге его схемы), но требует внешнего владельца фенса, которого `fbctl` предъявить не может. Поэтому его не вызывает никто. Даём ему тот же путь захвата, что и у `/vision/reconnect`.

- [ ] **Step 1: Переписать тест отказа без владельца на новое поведение**

Текущий тест `test_ensure_cdp_rejects_missing_platform_owner` фиксирует СТАРОЕ поведение (без владельца — отказ). Он должен быть заменён: пустой заголовок теперь означает «возьми фенс сам».

В `tests/unit/test_vision_ensure_cdp.py` удалить тест `test_ensure_cdp_rejects_missing_platform_owner` целиком (строки 68–83) и добавить в конец файла:

```python
class _FakeExclusiveMaintenance:
    """Фенс, который платформа берёт сама, когда владельца ей не передали."""

    instances: list[str] = []

    def __init__(self, _engine, *, operation_kind):
        self.operation_kind = operation_kind
        self.owner = "b" * 32

    async def __aenter__(self):
        _FakeExclusiveMaintenance.instances.append(self.operation_kind)
        return self

    async def assert_held(self):
        return None

    async def __aexit__(self, *_args):
        return False


@pytest.mark.asyncio
async def test_ensure_cdp_claims_its_own_fence_without_a_caller_owner(monkeypatch):
    """Деплою неоткуда взять владельца фенса: он его и не должен искать.

    Эндпоинт задуман для platform healer, а healer — это fbctl между стартом
    стола и проверкой канала. Требование готового владельца делало ручку
    невызываемой, поэтому без заголовка она берёт эксклюзив сама.
    """
    _FakeExclusiveMaintenance.instances.clear()
    recovered = {"called": False, "owner": ""}

    async def fake_probe(_client, *, expected_profile_id):
        assert expected_profile_id == "profile-exact"
        if recovered["called"]:
            return m._BrowserChannelProbe("READY", None, 1, True)
        return m._BrowserChannelProbe("DEGRADED", "BROWSER_UNAVAILABLE", 1, True)

    async def fake_recover(_engine, _settings, *, maintenance_owner):
        recovered["called"] = True
        recovered["owner"] = maintenance_owner

    monkeypatch.setattr(m, "BrowserExclusiveMaintenance", _FakeExclusiveMaintenance)
    monkeypatch.setattr(m, "_probe_browser_channel", fake_probe)
    monkeypatch.setattr(m, "_recover_browser_profile_under_maintenance", fake_recover)

    resp = await m.post_vision_ensure_cdp(
        request=SimpleNamespace(headers={}),
        engine=None,
        settings=None,
        meta_api_client=None,
    )

    assert resp.ok is True
    assert resp.status == "RECOVERED"
    assert recovered["called"] is True
    # Восстановление идёт под тем владельцем, которого выдал захваченный фенс.
    assert recovered["owner"] == "b" * 32
    assert _FakeExclusiveMaintenance.instances == ["vision_ensure_cdp"]


@pytest.mark.asyncio
async def test_ensure_cdp_still_adopts_an_explicit_owner(monkeypatch):
    """Вызов с готовым владельцем не должен захватывать второй фенс."""

    class UnexpectedExclusive:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("владелец передан — второй фенс брать нельзя")

    async def fake_probe(_client, *, expected_profile_id):
        return m._BrowserChannelProbe("READY", None, 1, True)

    monkeypatch.setattr(m, "BrowserExclusiveMaintenance", UnexpectedExclusive)
    monkeypatch.setattr(m, "_probe_browser_channel", fake_probe)

    resp = await m.post_vision_ensure_cdp(
        request=_request(),
        engine=None,
        settings=None,
        meta_api_client=None,
    )

    assert resp.ok is True
    assert resp.status == "READY"
```

- [ ] **Step 2: Прогнать тесты и убедиться, что новые падают**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/unit/test_vision_ensure_cdp.py -q`
Expected: FAIL — `AttributeError` или `AssertionError`, потому что `post_vision_ensure_cdp` при пустом заголовке уходит в ветку `BrowserMaintenanceOwnerInvalid` и возвращает `Platform maintenance ownership is missing or expired`.

- [ ] **Step 3: Научить эндпоинт брать фенс самому**

В `apps/api/routers/v1/settings_vision.py` заменить начало тела `post_vision_ensure_cdp` (получение владельца и вход в guard). Было:

```python
    maintenance_owner = request.headers.get(
        "X-FB-Agent-Browser-Maintenance-Owner",
        "",
    )
    try:
        guard = BrowserMaintenanceGuard(engine, maintenance_owner)
        async with guard:
```

Стало:

```python
    supplied_owner = request.headers.get(
        "X-FB-Agent-Browser-Maintenance-Owner",
        "",
    )
    try:
        # Владельца фенса предъявляет только тот, кто уже ведёт обслуживание.
        # Деплою предъявлять нечего, а чинить канал после пересоздания стола
        # нужно именно ему — поэтому эксклюзив берётся здесь же.
        fence: BrowserMaintenanceGuard | BrowserExclusiveMaintenance = (
            BrowserMaintenanceGuard(engine, supplied_owner)
            if supplied_owner
            else BrowserExclusiveMaintenance(engine, operation_kind="vision_ensure_cdp")
        )
        async with fence as guard:
            maintenance_owner = supplied_owner or guard.owner
```

Дальше по телу функции ничего не меняется: `maintenance_owner` уже определён и используется в `_recover_browser_profile_under_maintenance(...)`.

Новых импортов не требуется: `BrowserExclusiveMaintenance`, `BrowserMaintenanceGuard` и `BrowserOperationDrainTimeout` уже импортированы в этом модуле (строки 34, 36 и 39). `BrowserExclusiveMaintenance.__aenter__` возвращает сам объект, поэтому `guard.owner` — тот 32-символьный владелец, под которым захвачен фенс.

Расширить список перехватываемых исключений, чтобы занятый фенс не превращался в 5xx. Было:

```python
    except (
        BrowserMaintenanceOwnerInvalid,
        BrowserFenceLeaseLost,
        BrowserOperationBlocked,
    ) as exc:
```

Стало:

```python
    except (
        BrowserMaintenanceOwnerInvalid,
        BrowserFenceLeaseLost,
        BrowserOperationBlocked,
        BrowserOperationDrainTimeout,
    ) as exc:
```

- [ ] **Step 4: Прогнать тесты**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/unit/test_vision_ensure_cdp.py tests/unit/test_settings_vision_reconnect.py -q`
Expected: PASS, все тесты зелёные.

- [ ] **Step 5: Полный backend-прогон и линт**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/unit -q && .venv/bin/python -m ruff check . && .venv/bin/python -m ruff format --check .`
Expected: `2805 passed` или больше, `All checks passed!`, `762 files already formatted` или больше.

- [ ] **Step 6: Коммит**

```bash
git add apps/api/routers/v1/settings_vision.py tests/unit/test_vision_ensure_cdp.py
git commit -m "feat(vision): ensure-cdp сам берёт эксклюзив, когда владельца не передали

Ручка задумана для platform healer, но требовала готового владельца
maintenance-фенса — предъявить его деплою неоткуда, поэтому её не вызывал
никто. Без заголовка эксклюзив захватывается на месте, тем же путём, что и
у /vision/reconnect; с заголовком поведение прежнее."
```

---

### Task 2: Деплой поднимает канал стола сам

**Files:**
- Modify: `fbctl/probes.py` (протокол `ProbeClient`, класс `UrllibProbeClient`, новая функция после `require_exact_browser`)
- Modify: `fbctl/controller.py:170-189` (`REHEARSAL_FAILPOINTS`), `fbctl/controller.py:328-334` (последовательность шагов), новый метод рядом с `_verify_application`
- Test: `tests/unit/test_fbctl.py`

**Interfaces:**
- Consumes: `POST /api/vision/ensure-cdp` из задачи 1 — возвращает `{"ok": bool, "status": str, "action": str, "message": str}`, HTTP 200 всегда. Хелперы `api_headers(api_key) -> dict[str, str]`, `wait_for(description, check, *, timeout, interval, monotonic, sleep)`, `self._api_origin(config) -> str`, `config.api_key`.
- Produces: `ensure_browser_channel(client: ProbeClient, api_origin: str, api_key: str) -> None` — поднимает `FbctlError`, пока канал не готов. Шаг деплоя `ensure_desktop_channel` между `start_application` и `verify_application`.

**Почему отдельный шаг, а не «починка внутри verify»:** `verify_application` — гейт, он обязан только проверять. Починка — отдельное действие с собственным именем в логе и собственным failpoint, иначе в отчёте о деплое не видно, чинили канал или он поднялся сам.

- [ ] **Step 1: Написать падающий тест пробы**

Добавить в конец `tests/unit/test_fbctl.py`:

```python
class _EnsureChannelProbe:
    """Считает вызовы ensure-cdp и отдаёт заранее заданные ответы."""

    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, str]]] = []

    def post_json(self, url, payload, *, headers=None, timeout: float = 15):
        self.calls.append((url, dict(headers or {})))
        return 200, self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]


def test_deploy_heals_the_desktop_channel_before_verifying_it() -> None:
    """Пересоздание стола гасит браузерный канал, и деплой обязан его поднять.

    Каждый деплой пересоздаёт контейнер стола, после чего Vision стартует без
    CDP-профиля. Раньше это лечили руками между шагами деплоя, иначе
    verify_application падал на несовпадении живого профиля с каноническим.
    """
    probe = _EnsureChannelProbe([{"ok": True, "status": "RECOVERED", "action": "restart"}])

    fbctl_probes.ensure_browser_channel(probe, "http://api", "k" * 24)

    url, headers = probe.calls[0]
    assert url == "http://api/api/vision/ensure-cdp"
    assert headers["X-API-Key"] == "k" * 24


def test_unhealed_channel_stops_the_deploy_with_the_reason() -> None:
    probe = _EnsureChannelProbe(
        [{"ok": False, "status": "UNAVAILABLE", "message": "Browser-agent profile recovery failed"}]
    )

    with pytest.raises(FbctlError) as error:
        fbctl_probes.ensure_browser_channel(probe, "http://api", "k" * 24)

    assert "Browser-agent profile recovery failed" in str(error.value)


def test_channel_healing_runs_before_the_application_gate() -> None:
    """Порядок важен: сначала поднять канал, потом проверять его гейтом."""
    steps = fbctl_controller.REHEARSAL_FAILPOINTS

    assert "ensure_desktop_channel" in steps
    assert steps.index("start_application") < steps.index("ensure_desktop_channel")
    assert steps.index("ensure_desktop_channel") < steps.index("verify_application")
```

Добавить импорт модуля проб в шапку `tests/unit/test_fbctl.py` рядом с существующими импортами `fbctl`:

```python
from fbctl import probes as fbctl_probes
```

- [ ] **Step 2: Прогнать тесты и убедиться, что они падают**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/unit/test_fbctl.py -q -k "channel"`
Expected: FAIL — `AttributeError: module 'fbctl.probes' has no attribute 'ensure_browser_channel'` и `assert 'ensure_desktop_channel' in steps`.

- [ ] **Step 3: Добавить `post_json` в клиент проб**

В `fbctl/probes.py` в протокол `ProbeClient` после `patch_json` добавить:

```python
    def post_json(
        self,
        url: str,
        payload: Mapping[str, object],
        *,
        headers: Mapping[str, str] | None = None,
        timeout: float = 15,
    ) -> tuple[int, object]: ...
```

В класс `UrllibProbeClient` после метода `patch_json` добавить:

```python
    def post_json(
        self,
        url: str,
        payload: Mapping[str, object],
        *,
        headers: Mapping[str, str] | None = None,
        timeout: float = 15,
    ) -> tuple[int, object]:
        request_headers = {"Content-Type": "application/json", **dict(headers or {})}
        request = urllib.request.Request(
            url,
            data=json.dumps(dict(payload), separators=(",", ":")).encode("utf-8"),
            headers=request_headers,
            method="POST",
        )
        status, response = self._read(request, timeout=timeout)
        try:
            return status, json.loads(response)
        except (json.JSONDecodeError, UnicodeError) as exc:
            raise FbctlError(f"endpoint returned invalid JSON: {_safe_url(url)}") from exc
```

- [ ] **Step 4: Добавить пробу восстановления канала**

В `fbctl/probes.py` сразу после функции `require_exact_browser` добавить:

```python
def ensure_browser_channel(client: ProbeClient, api_origin: str, api_key: str) -> None:
    """Поднять браузерный канал после пересоздания стола.

    Каждый деплой пересоздаёт контейнер стола, и Vision возвращается без
    запущенного профиля. Ручка идемпотентна: готовый канал она не трогает.
    """
    status, payload = client.post_json(
        f"{api_origin}/api/vision/ensure-cdp",
        {},
        headers=api_headers(api_key),
        timeout=120,
    )
    if status != 200 or not isinstance(payload, dict):
        raise FbctlError("browser channel healer is unavailable")
    if payload.get("ok") is True:
        return
    message = payload.get("message")
    detail = str(message) if isinstance(message, str) and message else str(payload.get("status"))
    raise FbctlError(f"browser channel is not ready ({detail})")
```

- [ ] **Step 5: Зарегистрировать шаг деплоя**

В `fbctl/controller.py` в кортеж `REHEARSAL_FAILPOINTS` добавить `"ensure_desktop_channel"` между `"start_application"` и `"verify_application"`:

```python
    "start_application",
    "ensure_desktop_channel",
    "verify_application",
```

Шардирование rehearsal читает этот список динамически через `deploy --list-failpoints`, поэтому матрицу в `.github/workflows/release.yml` править НЕ нужно.

В последовательности шагов деплоя, сразу после строки со `start_application`, добавить:

```python
                self._step(
                    "ensure_desktop_channel",
                    options,
                    lambda: self._ensure_desktop_channel(config),
                )
```

Рядом с методом `_verify_application` добавить:

```python
    def _ensure_desktop_channel(self, config: RuntimeConfig) -> None:
        """Поднять браузерный канал до того, как его начнёт проверять гейт.

        Стол пересоздаётся каждым деплоем, поэтому канал после старта всегда
        нужно поднимать заново. Ручка идемпотентна, а браузер может стартовать
        не с первой попытки — отсюда ожидание, а не единичный вызов.
        """
        wait_for(
            "recovered browser channel",
            lambda: ensure_browser_channel(
                self.probes,
                self._api_origin(config),
                config.api_key,
            ),
            timeout=180,
            interval=5,
            monotonic=self.monotonic,
            sleep=self.sleep,
        )
```

Добавить `ensure_browser_channel` в импорт из `fbctl.probes` в шапке `fbctl/controller.py`.

- [ ] **Step 6: Прогнать тесты**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/unit/test_fbctl.py -q`
Expected: PASS, все тесты зелёные.

Тестовый двойник клиента проб в `tests/unit/test_fbctl.py` (класс с методами `json` и `patch_json`, метод `patch_json` на строке 544) теперь должен уметь и `post_json`. Добавить в него рядом с `patch_json`:

```python
    def post_json(self, url: str, payload, *, headers=None, timeout: float = 15):
        del headers, timeout
        assert url.endswith("/api/vision/ensure-cdp")
        assert payload == {}
        return 200, {"ok": True, "status": "READY", "action": "none", "message": ""}
```

- [ ] **Step 7: Полный backend-прогон и линт**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/unit -q && .venv/bin/python -m ruff check . && .venv/bin/python -m ruff format --check .`
Expected: всё зелёное.

- [ ] **Step 8: Коммит**

```bash
git add fbctl/probes.py fbctl/controller.py tests/unit/test_fbctl.py
git commit -m "feat(fbctl): деплой сам поднимает браузерный канал после старта стола

Стол пересоздаётся каждым деплоем, Vision возвращается без CDP-профиля, и
verify_application падал на несовпадении живого профиля с каноническим —
канал приходилось поднимать руками между шагами. Теперь это отдельный
идемпотентный шаг ensure_desktop_channel перед гейтом: в логе деплоя видно,
чинили канал или он поднялся сам."
```

---

### Task 3: Документация и лицензии описывают то, что реально в образе

**Files:**
- Modify: `DEPLOYMENT.md:13`, `DEPLOYMENT.md:15-17`
- Modify: `CLAUDE.md:104`
- Modify: `deploy/vision-webtop/THIRD_PARTY_NOTICES.md`
- Test: `tests/unit/test_vision_webtop.py`

**Interfaces:**
- Consumes: ничего из предыдущих задач.
- Produces: ничего для последующих задач.

**Почему это не косметика:** `THIRD_PARTY_NOTICES.md` — документ лицензионного соответствия. Он до сих пор объявляет KasmVNC (GPL-2.0) и Kasm noVNC (MPL-2.0), которых в образе больше нет, и обещает патчи веб-клиента, которых не существует: builder-стадия сборки веб-клиента удалена из Dockerfile.

- [ ] **Step 1: Написать падающий гард на notices**

Добавить в конец `tests/unit/test_vision_webtop.py`:

```python
def test_third_party_notices_describe_the_shipped_image() -> None:
    """Notices — документ соответствия, а не история образа.

    Снятый софт в нём хуже отсутствия: он объявляет обязательства по чужим
    лицензиям, которых образ уже не несёт.
    """
    notices = (WEBTOP / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    dockerfile = (WEBTOP / "Dockerfile").read_text(encoding="utf-8")

    lowered = notices.lower()
    assert "kasmvnc" not in lowered
    assert "novnc" not in lowered
    # Обещания про сборку веб-клиента: builder-стадии в образе больше нет.
    assert "web client" not in lowered

    # То, что реально ставится, обязано быть объявлено.
    assert "RustDesk" in notices
    assert "Vision" in notices
    assert "Firefox" in notices
    for token in ("RUSTDESK_VERSION", "FIREFOX_VERSION"):
        assert token in dockerfile
```

- [ ] **Step 2: Прогнать тест и убедиться, что он падает**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/unit/test_vision_webtop.py -q -k notices`
Expected: FAIL — `assert "kasmvnc" not in lowered`.

- [ ] **Step 3: Переписать notices**

Заменить содержимое `deploy/vision-webtop/THIRD_PARTY_NOTICES.md` целиком на:

```markdown
# Third-party notices

- Ubuntu 24.04 base and distribution packages retain their respective licenses.
- Vision 3.6.8 is installed from the vendor package pinned by SHA-256; its vendor license applies.
- Mozilla Firefox 140.13.0esr is installed from the official Mozilla tarball pinned by SHA-256, MPL-2.0.
- RustDesk 1.4.6 is installed from the official GitHub release package pinned by SHA-256, AGPL-3.0-or-later. It is used unmodified.
```

- [ ] **Step 4: Поправить DEPLOYMENT.md**

Строку 13 заменить:

```markdown
- `fb_agent_desktop` — Vision, browser-agent и брокеры RustDesk;
```

Абзац про Caddy (строки 15–17) заменить на:

```markdown
Caddy всегда направляет трафик на `18100` (API), `18080` (web) и `18081`
(TMA). Доступ к рабочему столу веб-канала не имеет: он идёт нативным
клиентом RustDesk через собственный брокер в приватной сети. Docker
`restart: unless-stopped` отвечает за запуск после reboot; отдельных
application systemd units нет.
```

- [ ] **Step 5: Поправить CLAUDE.md**

Строку 104 заменить:

```markdown
`services/browser-agent/` — Node.js gRPC слой рядом с независимым Vision
```

- [ ] **Step 6: Прогнать тесты**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/unit/test_vision_webtop.py -q`
Expected: PASS, все тесты зелёные.

- [ ] **Step 7: Коммит**

```bash
git add DEPLOYMENT.md CLAUDE.md deploy/vision-webtop/THIRD_PARTY_NOTICES.md tests/unit/test_vision_webtop.py
git commit -m "docs(desktop): привести описание и лицензии к тому, что реально в образе

Notices объявляли KasmVNC и Kasm noVNC, которых в образе нет, и обещали
патчи веб-клиента, чья builder-стадия удалена. DEPLOYMENT.md всё ещё вёл
Caddy на порт 8444 снятого веб-канала. Гард держит notices в соответствии
с содержимым образа."
```

---

### Task 4: Строки канала не разъезжаются на телефоне, и оператор видит условие доступа

**Files:**
- Modify: `frontend/src/routes/remote-desktop/index.tsx` (компонент `ChannelRow` + абзац подсказки)
- Modify: `frontend-mini/src/routes/desktop/index.tsx` (компонент `ChannelRow` + абзац подсказки)
- Test: `frontend/src/tests/pages/RemoteDesktop.test.tsx`, `frontend-mini/src/tests/Desktop.test.tsx`

**Дефект вёрстки (найден владельцем на живом телефоне):** ключ брокера —
длинная строка без пробелов — уезжает под кнопку копирования и вылезает за
карточку. Причина: строки канала лежат в `<dl className="grid gap-2">`, а у
grid-элемента `min-width` по умолчанию `auto`, то есть он не может сжаться
уже своего содержимого. Внутренний `min-w-0` при этом бесполезен, и
`truncate` не срабатывает никогда. Лечится `min-w-0` на самом grid-элементе —
корневом `<div>` компонента `ChannelRow`. Дефект есть в обоих фронтах; на
широком экране он просто не заметен.

**Interfaces:**
- Consumes: `useDesktopNativeChannel()` из `frontend/src/lib/api/desktop.ts` и инлайновый `tmaApi.useQuery("get", "/api/desktop/native", ...)` в мини-аппе — оба отдают `{ available, server, key, device_id }`.
- Produces: ничего для последующих задач.

**Почему:** брокер слушает только приватный адрес, поэтому с устройства без Tailscale подключение не состоится вообще — и без подсказки это выглядит как поломка стола, а не как отсутствующий VPN. Текущая формулировка «Сервер доступен только из приватной сети» верна, но не говорит, что именно включить.

- [ ] **Step 1: Написать падающие тесты веб-страницы**

В `frontend/src/tests/pages/RemoteDesktop.test.tsx` добавить внутрь `describe("RemoteDesktopPage", ...)`:

```tsx
  it("называет условие доступа, а не только факт приватной сети", () => {
    render(<RemoteDesktopPage />);

    expect(screen.getByText(/Tailscale/)).toBeInTheDocument();
  });

  it("не даёт длинному ключу разъехать строку канала", () => {
    render(<RemoteDesktopPage />);

    // Строка канала — grid-элемент, а у него min-width по умолчанию auto:
    // без min-w-0 он не сожмётся, и длинный ключ уедет под кнопку копирования.
    const key = screen.getByText("QJztruGKKjvEcX9XBLMixf21wieLGYABEaWby97JP5s=");
    const row = key.closest("div")!.parentElement!;

    expect(row.className).toContain("min-w-0");
    expect(key.className).toContain("truncate");
  });
```

- [ ] **Step 2: Написать падающие тесты мини-аппа**

В `frontend-mini/src/tests/Desktop.test.tsx` добавить внутрь `describe("Mini App RemoteDesktopPage", ...)`:

```tsx
  it("называет условие доступа, а не только факт приватной сети", () => {
    render(<RemoteDesktopPage />);

    expect(screen.getByText(/Tailscale/)).toBeInTheDocument();
  });

  it("не даёт длинному ключу разъехать строку канала", () => {
    render(<RemoteDesktopPage />);

    // На телефоне это видно глазом: ключ брокера уезжал под кнопку копирования.
    const key = screen.getByText("QJztruGKKjvEcX9XBLMixf21wieLGYABEaWby97JP5s=");
    const row = key.closest("div")!.parentElement!;

    expect(row.className).toContain("min-w-0");
    expect(key.className).toContain("truncate");
  });
```

- [ ] **Step 3: Прогнать оба теста и убедиться, что падают**

Run: `cd frontend && pnpm test -- RemoteDesktop`
Expected: FAIL — `Unable to find an element with the text: /Tailscale/`.

Run: `cd frontend-mini && pnpm test -- Desktop`
Expected: FAIL — то же самое.

- [ ] **Step 4: Дописать подсказку на веб-странице**

В `frontend/src/routes/remote-desktop/index.tsx` заменить абзац подсказки:

```tsx
                <p className="mx-auto mt-4 max-w-[460px] text-[12px] leading-5 text-bg-8">
                  Первая настройка клиента: Settings → Network → ID/Relay Server — адрес сервера и
                  ключ выше. Сервер живёт в приватной сети: на устройстве должен быть включён
                  Tailscale, иначе подключение не дойдёт.
                </p>
```

- [ ] **Step 5: Дописать подсказку в мини-аппе**

В `frontend-mini/src/routes/desktop/index.tsx` заменить абзац под заголовком:

```tsx
              <p className="mt-1 text-[12px] leading-relaxed text-bg-9">
                Через приложение RustDesk и приватную сеть: на телефоне должен быть включён
                Tailscale. Пароль канала приложение запомнит после первого подключения.
              </p>
```

- [ ] **Step 5a: Починить сжатие строки канала в обоих фронтах**

В обоих файлах у компонента `ChannelRow` добавить `min-w-0` в класс корневого `<div>`. Было (одинаково в вебе и мини-аппе, отличаются только скругление и размер кнопки):

```tsx
    <div className="flex items-center justify-between gap-3 rounded-[var(--radius-2)] border border-[var(--color-hairline)] bg-bg-2 px-3 py-2">
```

Стало:

```tsx
    <div className="flex min-w-0 items-center justify-between gap-3 rounded-[var(--radius-2)] border border-[var(--color-hairline)] bg-bg-2 px-3 py-2">
```

Больше ничего в компоненте не менять: `min-w-0` на внутреннем блоке и `truncate` на значении уже стоят и заработают, как только сожмётся сам grid-элемент.

- [ ] **Step 6: Прогнать тесты обоих фронтов**

Run: `cd frontend && pnpm test`
Expected: PASS — `Test Files 55 passed`, `Tests 503 passed` (на два больше прежнего).

Run: `cd frontend-mini && pnpm test`
Expected: PASS — `Test Files 32 passed`, `Tests 186 passed`.

- [ ] **Step 7: Проверить типы, линт и типографский гард**

Run: `pnpm -r typecheck && pnpm -r lint`
Expected: обе команды завершаются `Done` без ошибок. Гард типографики требует размера шрифта не меньше 12px — в правках использованы `text-[12px]`.

- [ ] **Step 8: Коммит**

```bash
git add frontend/src/routes/remote-desktop/index.tsx frontend-mini/src/routes/desktop/index.tsx frontend/src/tests/pages/RemoteDesktop.test.tsx frontend-mini/src/tests/Desktop.test.tsx
git commit -m "fix(desktop-ui): починить строку канала на телефоне и назвать условие доступа

Ключ брокера — длинная строка без пробелов — уезжал под кнопку копирования:
строки канала лежат в grid, а grid-элемент с min-width auto не сжимается
уже содержимого, поэтому truncate не срабатывал никогда.

Заодно брокер слушает только приватный адрес, и с устройства без Tailscale
подключение не доходит вовсе — без явной подсказки это читается как
поломка стола, а не как выключенный VPN."
```

---

### Task 5: Выкатить и убрать временный костыль с прод-хоста

**Files:**
- Delete (на хосте): `/root/vision-channel-heal.sh`, `/root/vision-channel-heal.log`

**Interfaces:**
- Consumes: шаг `ensure_desktop_channel` из задачи 2 — он заменяет ручной запуск host-скрипта.
- Produces: ничего.

**Важно:** до этой задачи должны быть смержены задачи 1–4. Скрипт удаляется ТОЛЬКО после зелёного релиза, в котором `ensure_desktop_channel` отработал сам — иначе следующий деплой останется без страховки.

- [ ] **Step 1: Убедиться, что main содержит обе backend-задачи**

Run: `git fetch origin && git log --oneline origin/main -5`
Expected: в списке есть коммиты `feat(vision): ensure-cdp сам берёт эксклюзив...` и `feat(fbctl): деплой сам поднимает браузерный канал...`.

- [ ] **Step 2: Запустить релиз со страховкой**

Наблюдатель остаётся вооружённым на случай, если новый шаг не сработает: это первый прогон, страховка снимается только после успеха.

```bash
ssh root@62.60.150.133 'rm -f /root/vision-channel-heal.log; old=$(docker inspect fb_agent_desktop-vision-webtop-1 --format "{{.Id}}"); setsid nohup /root/vision-channel-heal.sh "$old" >/dev/null 2>&1 & sleep 2; pgrep -f vision-channel-heal >/dev/null && echo armed'
gh workflow run release.yml --ref main
```

Expected: `armed`, затем URL запущенного workflow.

- [ ] **Step 3: Дождаться результата релиза**

Run: `gh run list --workflow=release.yml --limit 1 --json databaseId,status,conclusion -q '.[] | "\(.databaseId) \(.status) \(.conclusion // "")"'`

Повторять примерно каждые 5 минут: полный прогон занимает около 25 минут (verify → сборка образов → 5 rehearsal-джоб → деплой).
Expected: `completed success`.

- [ ] **Step 4: Убедиться, что канал поднял именно деплой**

```bash
gh run view <RUN_ID> --log | grep -E "step=ensure_desktop_channel|step=verify_application"
ssh root@62.60.150.133 'cat /root/vision-channel-heal.log'
```

Expected: в логе деплоя есть `step=ensure_desktop_channel completed` и следом `step=verify_application completed`. В логе наблюдателя либо пусто, либо он сработал позже завершения шага — тогда канал поднял деплой. Если наблюдатель успел раньше, повторить релиз с невооружённым наблюдателем и убедиться, что шаг справляется один.

- [ ] **Step 5: Проверить прод**

```bash
ssh root@62.60.150.133 'docker ps --format "{{.Names}} {{.Status}}" | grep -cE "app|desktop"; readlink /opt/fb-agent/runtime; cat /opt/fb-agent/shared/desktop-readiness/rustdesk.json'
```

Expected: 18 контейнеров, `runtime` указывает на payload свежего релиза, `rustdesk.json` содержит `server`, `key` и числовой `device_id`.

- [ ] **Step 6: Убрать костыль**

```bash
ssh root@62.60.150.133 'rm -f /root/vision-channel-heal.sh /root/vision-channel-heal.log && echo removed'
```

Expected: `removed`.

- [ ] **Step 7: Зафиксировать в памяти проекта**

Обновить `~/.claude/projects/-Users-markvasilev-Desktop-FB-Agent/memory/project-release-gate-scanning-and-vision.md`: раздел про ручное лечение канала Vision пометить закрытым, указать, что его выполняет шаг деплоя `ensure_desktop_channel`, и убрать упоминание host-скрипта. Строку в `MEMORY.md` привести в соответствие.

---

### Task 6: Ни один экран не обрезает содержимое

**Files:**
- Modify: `frontend/src/features/operator/operator-ledger.css` (правило `.ledger-approaching-item__meta`)
- Modify: `frontend-mini/src/features/operator/operator-mini-ledger.css` (правило `.mini-approaching-item__meta`)
- Modify: `frontend/src/components/offers/OfferCard.tsx` (футер действий)
- Modify: `frontend/src/components/layout/CommandPalette.tsx`, `frontend/src/components/analytics/PerformanceTable.tsx`, `frontend/src/components/domain/campaigns/WizardStep6Preview.tsx`, `frontend/src/components/domain/assistant/AssistantPanel.tsx`, `frontend/src/components/layout/WorkerPulse.tsx`, `frontend/src/components/history/HistoryTimeline.tsx`, `frontend/src/components/domain/campaigns/CampaignRunsHistory.tsx`
- Create: `frontend/src/tests/guards/shrinkable-truncate.test.ts`
- Test: `frontend/src/tests/routes/offers.test.tsx`

**Interfaces:**
- Consumes: ничего из предыдущих задач.
- Produces: ничего для последующих задач.

**Откуда список:** владелец прислал два скриншота с живого экрана, разведка нашла остальные места того же класса. Правило, которое здесь нарушается: у flex- и grid-элемента `min-width` по умолчанию равен `auto`, то есть элемент не может стать уже своего содержимого. Пока на нём нет `min-w-0`, стоящий внутри `truncate` не сработает никогда — контейнер просто расширится и вытолкнет содержимое за карточку. Второй вариант того же — `flex: 1` (это `flex: 1 1 0%`), который раздаёт равные доли независимо от длины подписей.

- [ ] **Step 1: Написать защитный тест, который ловит весь класс разом**

Создать `frontend/src/tests/guards/shrinkable-truncate.test.ts`:

```ts
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, resolve } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * Сжимаемость важнее вкуса: у flex- и grid-элемента min-width по умолчанию
 * auto, поэтому элемент не может стать уже содержимого. Пока на нём нет
 * min-w-0, соседний truncate не срабатывает никогда — длинное имя кампании
 * или ключ брокера выталкивает карточку за экран. Владелец находил это
 * дважды на живом телефоне; тест держит класс дефекта закрытым.
 */

const ROOTS = [
  resolve(__dirname, "../../.."),
  resolve(__dirname, "../../../../frontend-mini"),
];

function sourceFiles(dir: string): string[] {
  const found: string[] = [];
  for (const entry of readdirSync(dir)) {
    if (entry === "node_modules" || entry === "dist" || entry === "tests") continue;
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      found.push(...sourceFiles(full));
    } else if (full.endsWith(".tsx")) {
      found.push(full);
    }
  }
  return found;
}

/** Значения className в одном атрибуте: только строковые литералы. */
function classNameLiterals(source: string): string[] {
  return [...source.matchAll(/className=(?:"([^"]*)"|\{`([^`]*)`\})/g)].map(
    (match) => match[1] ?? match[2] ?? "",
  );
}

describe("сжимаемость обрезаемых строк", () => {
  it("не оставляет truncate на элементе, который не умеет сжиматься", () => {
    const offenders: string[] = [];

    for (const root of ROOTS) {
      for (const file of sourceFiles(join(root, "src"))) {
        const source = readFileSync(file, "utf8");
        for (const classes of classNameLiterals(source)) {
          const words = classes.split(/\s+/);
          const shrinks = words.includes("flex-1") || words.includes("basis-0");
          const clips = words.includes("truncate");
          if (shrinks && clips && !words.includes("min-w-0")) {
            offenders.push(`${relative(root, file)}: ${classes}`);
          }
        }
      }
    }

    expect(offenders).toEqual([]);
  });
});
```

- [ ] **Step 2: Прогнать защитный тест и увидеть список нарушителей**

Run: `cd frontend && pnpm test -- shrinkable-truncate`
Expected: FAIL — в списке `offenders` окажутся как минимум `src/components/layout/CommandPalette.tsx`, `src/components/domain/campaigns/WizardStep6Preview.tsx` (две строки). Выпиши получившийся список: он и есть точный объём Step 4.

- [ ] **Step 3: Написать падающий тест на футер карточки оффера**

В `frontend/src/tests/routes/offers.test.tsx` добавить тест (внутрь существующего describe для карточки оффера; если такого нет — в конец файла, следуя тому, как в этом файле рендерят карточку):

```tsx
  it("не обрезает длинную подпись действия на узкой карточке", () => {
    // «Деактивировать» вдвое длиннее «Правила»: равные доли flex:1 её не вмещают,
    // и на живом экране текст уезжал за карточку.
    renderOffersPage();

    const deactivate = screen.getByRole("button", { name: /Деактивировать оффер/ });
    const footer = deactivate.parentElement!;

    expect(footer.style.flexWrap).toBe("wrap");
    expect(deactivate.style.flex).toBe("1 1 auto");
  });
```

Если хелпера `renderOffersPage` в файле нет — используй тот способ рендера, который уже применяют соседние тесты этого файла.

- [ ] **Step 4: Починить все найденные места**

`frontend/src/components/offers/OfferCard.tsx` — футер учится переносить, кнопки перестают делить ширину поровну. Было:

```tsx
      <footer
        style={{
          borderTop: "1px solid var(--color-hairline)",
          padding: "var(--space-3) var(--space-4)",
          display: "flex",
          gap: "var(--space-2)",
        }}
      >
```

Стало:

```tsx
      <footer
        style={{
          borderTop: "1px solid var(--color-hairline)",
          padding: "var(--space-3) var(--space-4)",
          display: "flex",
          flexWrap: "wrap",
          gap: "var(--space-2)",
        }}
      >
```

И у всех трёх кнопок этого футера заменить `style={{ flex: 1 }}` на `style={{ flex: "1 1 auto" }}`: базис по содержимому вместо нуля даёт длинной подписи её ширину, а свободное место по-прежнему делится.

`frontend/src/features/operator/operator-ledger.css` — в правило `.ledger-approaching-item__meta` добавить `min-width: 0;`. То же в `frontend-mini/src/features/operator/operator-mini-ledger.css` для `.mini-approaching-item__meta`. Оба правила уже имеют `overflow: hidden` и `text-overflow: ellipsis`, но лежат в grid-элементе и потому не сжимаются: в мини-аппе это главный экран, где выводится реальное имя FB-кампании.

В следующих местах добавить `min-w-0` в тот же `className`, ничего больше не меняя:

- `frontend/src/components/layout/CommandPalette.tsx:245` — было `className="flex-1 truncate"`, стало `className="min-w-0 flex-1 truncate"`
- `frontend/src/components/analytics/PerformanceTable.tsx:355` — было `className="truncate text-[14px] font-medium text-bg-11"`, стало `className="min-w-0 truncate text-[14px] font-medium text-bg-11"`
- `frontend/src/components/domain/campaigns/WizardStep6Preview.tsx:248` — было `className="text-[12px] text-bg-11 flex-1 truncate"`, стало `className="min-w-0 text-[12px] text-bg-11 flex-1 truncate"`
- `frontend/src/components/domain/campaigns/WizardStep6Preview.tsx:260` — было `className="text-[12px] text-bg-9 flex-1 truncate"`, стало `className="min-w-0 text-[12px] text-bg-9 flex-1 truncate"`
- `frontend/src/components/domain/assistant/AssistantPanel.tsx:51` — добавить `min-w-0` в className того `span`, где стоит `truncate max-w-[120px]`
- `frontend/src/components/layout/WorkerPulse.tsx:119` — добавить `min-w-0` в className того `span`, где стоит `truncate`
- `frontend/src/components/history/HistoryTimeline.tsx:180` — добавить `min-w-0` в className того `span`, где стоит `truncate`

`frontend/src/components/domain/campaigns/CampaignRunsHistory.tsx:240` — здесь `truncate` навешан на целое предложение, и оно обрезается на середине слова. Заменить `truncate` на `line-clamp-2`: в мини-аппе тот же текст уже так и сделан (`frontend-mini/src/routes/campaigns/RunsHistory.tsx:673`).

Если защитный тест из Step 2 показал места, не перечисленные выше, — почини и их тем же способом и перечисли их в отчёте.

- [ ] **Step 5: Прогнать тесты**

Run: `cd frontend && pnpm test`
Expected: PASS, включая новый `shrinkable-truncate` и новый тест футера карточки оффера.

Run: `cd frontend-mini && pnpm test`
Expected: PASS без изменений в количестве тестов (там менялся только CSS).

- [ ] **Step 6: Проверить типы и линт**

Run: `pnpm -r typecheck && pnpm -r lint`
Expected: обе команды завершаются без ошибок.

- [ ] **Step 7: Коммит**

```bash
git add frontend/src frontend-mini/src
git commit -m "fix(ui): не давать длинным значениям выталкивать содержимое за карточку

У flex- и grid-элемента min-width по умолчанию auto: элемент не может стать
уже содержимого, поэтому стоящий внутри truncate не срабатывает никогда, а
длинное имя кампании или ключ брокера выталкивает карточку за экран.
Владелец нашёл это дважды на живом телефоне; разведка нашла остальные места
того же класса.

Футер карточки оффера чинится иначе: три кнопки делили ширину поровну
(flex: 1 1 0%), и подпись «Деактивировать» в свою треть не влезала.

Защитный тест держит класс дефекта закрытым в обоих фронтах."
```

---

### Task 7: Выключенное сканирование перестаёт выглядеть чередой отказов

**Files:**
- Modify: `core/observer/scan_tasks.py` (функция `enqueue_scheduled_observer_scan`, строка 191)
- Modify: `apps/observer_worker/main.py:1032-1050` (финализация исхода скана)
- Test: `tests/unit/test_observer_scan_action_lifecycle.py`

**Interfaces:**
- Consumes: `load_scanning_enabled(engine) -> bool` из `core/observer/queries.py:188` — единая точка чтения «глобального стопа», лёгкий одиночный SELECT, ровно для воркеров, которые должны замирать на паузе. Нет строки `observer_config` → `False` (fail-safe). Ошибку соединения не глушит.
- Produces: ничего для последующих задач.

**Что сломано (замерено на проде 16.08):** `observer_scheduler` публикует адаптивный скан каждые ~45 секунд независимо от того, включено ли сканирование. Скан немедленно возвращает исход `paused`, а финализатор считает провалом всё, что не `success` и не `empty`, — и записывает `observer scan finished without a complete snapshot: paused`. За четыре часа накопилось 216 таких записей; они попадают в операторскую ленту «Действия» красным и хоронят под собой настоящие отказы (там же лежали 7 реальных `tracker_event_process`). Выключенное владельцем сканирование — состояние, а не отказ.

**Порядок лечения:** сначала не публиковать работу, которой заведомо не будет; финализация `paused` — вторая линия обороны для гонки «сканирование выключили между публикацией и запуском».

- [ ] **Step 1: Написать падающий тест на то, что на паузе скан не публикуется**

Добавить в `tests/unit/test_observer_scan_action_lifecycle.py`:

```python
@pytest.mark.asyncio
async def test_paused_scanning_publishes_no_adaptive_scan(monkeypatch) -> None:
    """Пауза владельца — это состояние, а не очередь отказов.

    Планировщик тикал каждые ~45 секунд и на выключенном сканировании
    публиковал задачу, которая гарантированно заканчивалась исходом paused.
    За четыре часа это дало 216 красных записей в ленте оператора.
    """
    published: list[str] = []

    async def fake_enqueue(*_args, **kwargs):
        published.append(str(kwargs.get("reason")))
        raise AssertionError("на паузе адаптивный скан публиковаться не должен")

    async def fake_scanning_enabled(_engine) -> bool:
        return False

    monkeypatch.setattr(scan_tasks, "load_scanning_enabled", fake_scanning_enabled)
    monkeypatch.setattr(scan_tasks, "enqueue_observer_scan", fake_enqueue)

    receipt = await scan_tasks.enqueue_scheduled_observer_scan(_engine_stub())

    assert receipt is None
    assert published == []
```

Имя импортированного модуля и способ подмены подгони под то, как это уже сделано в соседних тестах файла; `_engine_stub` — тот двойник движка, который в файле уже используется. Если в файле нет готового двойника, возьми способ из ближайшего теста, который вызывает функции `scan_tasks`.

- [ ] **Step 2: Написать падающий тест на то, что исход `paused` не отказ**

```python
@pytest.mark.asyncio
async def test_paused_outcome_is_not_recorded_as_a_failure(monkeypatch) -> None:
    """Гонка: сканирование выключили между публикацией и запуском задачи.

    Такая задача не выполнена, но и не провалена — оператор не должен видеть
    красное там, где сработал его собственный выключатель.
    """
    finalized: dict[str, object] = {}

    async def fake_mark_failed(*_args, **kwargs):
        raise AssertionError("исход paused не должен финализироваться как провал")

    async def fake_mark_cancelled(_engine, **kwargs):
        finalized.update(kwargs)
        return True

    # Подмена финализаторов и запуск ветки с summary={"outcome": "paused"} —
    # способ вызова возьми из соседнего теста этого файла, который уже
    # прогоняет финализацию скана с готовым summary.
```

Дописать тест до конца по образцу соседнего теста файла (тот, что проверяет `error="observer scan finished without a complete snapshot: partial"` на строке 85): нужен тот же способ вызова, но с исходом `paused` и проверкой, что задача финализирована как отменённая, а не проваленная.

- [ ] **Step 3: Прогнать тесты и убедиться, что падают**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/unit/test_observer_scan_action_lifecycle.py -q`
Expected: FAIL — оба новых теста, потому что публикация безусловна, а `paused` уходит в `mark_failed`.

- [ ] **Step 4: Не публиковать адаптивный скан на паузе**

В `core/observer/scan_tasks.py` в начале `enqueue_scheduled_observer_scan` (сигнатура на строке 191) добавить проверку до захвата advisory-лока:

```python
    # Публиковать нечего: на паузе скан немедленно вернёт outcome=paused, и
    # каждая такая задача осядет в ленте оператора как отказ. Точка чтения
    # та же, что у остальных воркеров, замирающих на глобальном стопе.
    if not await load_scanning_enabled(engine):
        return None
```

Тип возврата функции станет `ObserverScanReceipt | None` — поправить аннотацию и докстринг. Проверить единственного вызывающего (`apps/observer_worker/main.py:1134`): он результат не использует, но убедись, что `None` там ничего не ломает.

Импорт `load_scanning_enabled` добавить из `core.observer.queries`.

- [ ] **Step 5: Исход `paused` финализировать как отменённую задачу**

В `apps/observer_worker/main.py` в блоке финализации (строки 1032-1047) развести три случая вместо двух. Было:

```python
    if scan_outcome in {"success", "empty"}:
        finalized = await mark_succeeded(engine, result=task_result, **fence)
    else:
        finalized = await mark_failed(
            engine,
            error=f"observer scan finished without a complete snapshot: {scan_outcome}",
            result=task_result,
            **fence,
        )
```

Стало:

```python
    if scan_outcome in {"success", "empty"}:
        finalized = await mark_succeeded(engine, result=task_result, **fence)
    elif scan_outcome == "paused":
        # Сканирование выключил владелец: задача не выполнена, но и не
        # провалена. Красное здесь означало бы поломку там, где сработал
        # собственный выключатель оператора.
        finalized = await mark_cancelled(
            engine,
            reason="scanning_paused",
            result=task_result,
            **fence,
        )
    else:
        finalized = await mark_failed(
            engine,
            error=f"observer scan finished without a complete snapshot: {scan_outcome}",
            result=task_result,
            **fence,
        )
```

Точное имя и сигнатуру финализатора отмены возьми из того же модуля, откуда импортированы `mark_succeeded`/`mark_failed`. Если функции отмены с такой сигнатурой нет — **остановись и сообщи**, не изобретай новую финализацию задач: это money-путь.

**Обязательная проверка перед коммитом:** убедись, что ни одна логика свежести снимка и ни один money-путь не выводят «снимок обновился» из статуса задачи скана. Проверь потребителей: `core/meta_api/freshness.py` (там `snapshot_is_fresh` считает свежесть по данным скана, а не по статусу задачи) и всё, что читает `task_queue.status` для `observer_scan`. Результат проверки опиши в отчёте — это условие приёмки задачи.

- [ ] **Step 6: Прогнать тесты**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/unit -q && .venv/bin/python -m ruff check . && .venv/bin/python -m ruff format --check .`
Expected: всё зелёное.

- [ ] **Step 7: Коммит**

```bash
git add core/observer/scan_tasks.py apps/observer_worker/main.py tests/unit/test_observer_scan_action_lifecycle.py
git commit -m "fix(observer): не превращать выключенное сканирование в череду отказов

Планировщик публиковал адаптивный скан каждые ~45 секунд независимо от
глобального стопа. Скан немедленно возвращал исход paused, а финализатор
считал провалом всё, что не success и не empty: за четыре часа на проде
накопилось 216 красных записей, под которыми потерялись настоящие отказы.

Теперь на паузе работа не публикуется вовсе, а исход paused — если
сканирование выключили между публикацией и запуском — финализируется как
отменённая задача, а не как провал."
```

---

### Task 8: Завершённые задачи не копятся в очереди вечно

**Files:**
- Modify: `apps/cleanup_worker/worker.py` (рядом с `_PARTITIONED`, строка 31)
- Test: `tests/unit/` — файл тестов cleanup-воркера (найди существующий по имени модуля; если его нет, создай `tests/unit/test_cleanup_task_queue_retention.py`)

**Interfaces:**
- Consumes: `load_policy(engine)` из `apps/cleanup_worker/worker.py:44` — читает `retention_policy` из `system_config`, иначе дефолт.
- Produces: ничего для последующих задач.

**Что сломано:** `cleanup_worker` чистит только пять партиционированных таблиц (`ad_metrics`, `alert_events`, `scan_runs`, `meta_api_audit_log`, `adsetpro_postback_events`) — их список на строке 31. `task_queue` не партиционирована и не входит ни в один путь ретенции, поэтому завершённые задачи лежат вечно. На проде это уже сотни строк за сутки.

- [ ] **Step 1: Написать падающий тест**

Тест должен проверять поведение, а не наличие строки: завершённые задачи старше срока удаляются, а незавершённые и свежие — остаются. Работать он должен на тестовой БД тем же способом, каким тестируется остальной cleanup-воркер (посмотри существующие тесты этого модуля и повтори их устройство: фикстура движка, создание таблицы, вызов функции).

Проверить минимум три случая: (1) `succeeded`/`failed`/`cancelled` старше границы — удаляются; (2) такие же, но свежие — остаются; (3) `pending`/`running`/`retrying` любого возраста — остаются всегда, даже если застряли. Третий случай — money-инвариант: незавершённую задачу удалять нельзя ни при каких условиях, иначе исчезнет след незакрытой команды.

- [ ] **Step 2: Прогнать и убедиться, что падает**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/unit -q -k "task_queue and retention"`
Expected: FAIL — функции ретенции для `task_queue` ещё нет.

- [ ] **Step 3: Реализовать ретенцию**

Добавить в `apps/cleanup_worker/worker.py` отдельную функцию ретенции для непартиционированной `task_queue` и вызвать её из того же места, откуда выполняется остальная чистка. Требования:

- удаляются только терминальные статусы (`succeeded`, `failed`, `cancelled`) — список статусов возьми из модели/схемы `task_queue`, не выдумывай;
- граница берётся по `completed_at`, а при его отсутствии по `updated_at`;
- срок читается из `retention_policy` по собственному ключу `task_queue`, дефолт — 30 дней; поддержи то же «специальное» значение, что уже понимает `retention.py` (посмотри `is_special`);
- удаление батчами с ограничением на батч, чтобы одна уборка не держала долгую транзакцию на боевой таблице очереди;
- количество удалённого логируется.

- [ ] **Step 4: Прогнать тесты**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/unit -q && .venv/bin/python -m ruff check . && .venv/bin/python -m ruff format --check .`
Expected: всё зелёное.

- [ ] **Step 5: Коммит**

```bash
git add apps/cleanup_worker/worker.py tests/unit
git commit -m "feat(cleanup): чистить завершённые задачи очереди по ретенции

task_queue не партиционирована и не входила ни в один путь ретенции, поэтому
завершённые задачи лежали вечно. Незавершённые не удаляются никогда: пропажа
следа незакрытой команды опаснее роста таблицы."
```

---

### Task 9: Повторы в ленте действий не выглядят двадцатью разными событиями

**Files:**
- Modify: `frontend/src/features/operator/OperatorDashboard.tsx` (компонент `ActionList`, строка 743)
- Test: `frontend/src/tests/pages/OperatorActionsRealtime.test.tsx`

**Interfaces:**
- Consumes: `ActionList({ items }: { items: OperatorActionItem[] })` — общий компонент, который используют и дашборд, и страница `/actions` (`frontend/src/routes/actions/index.tsx:141`). Правка в одном месте покрывает оба экрана.
- Produces: ничего для последующих задач.

**Зачем:** двадцать одинаковых строк подряд читаются как двадцать разных событий и вытесняют с экрана всё остальное. После Task 7 поток таких повторов иссякнет, но повторы возможны у любого типа действий, и лента должна оставаться читаемой сама по себе.

- [ ] **Step 1: Написать падающий тест**

В `frontend/src/tests/pages/OperatorActionsRealtime.test.tsx` добавить тест: если подряд идут несколько записей с одинаковыми заголовком, состоянием и текстом, лента показывает одну строку со счётчиком повторов, а не несколько одинаковых. Счётчик должен быть виден как текст (например «×3»), а сама строка — вести к самому свежему из повторов.

Способ рендера и построения `items` возьми из соседних тестов этого файла.

- [ ] **Step 2: Прогнать и убедиться, что падает**

Run: `cd frontend && pnpm test -- OperatorActionsRealtime`
Expected: FAIL — сейчас рисуются все элементы подряд.

- [ ] **Step 3: Сгруппировать подряд идущие повторы**

В `ActionList` сворачивать ТОЛЬКО соседние элементы с одинаковыми заголовком, состоянием и текстом. Требования:

- группируются только подряд идущие: хронология не должна перемешиваться;
- у свёрнутой группы показывается счётчик повторов и время самого свежего элемента;
- переход по свёрнутой строке ведёт к самому свежему элементу группы;
- одиночный элемент выглядит ровно как раньше — без счётчика и без лишней обёртки;
- ключи React берутся от самого свежего элемента группы, а не от индекса.

Не менять API, не добавлять фильтров и не трогать серверную часть: это правка представления.

- [ ] **Step 4: Прогнать тесты и гейты**

Run: `cd frontend && pnpm test`
Expected: PASS.

Run: `pnpm -r typecheck && pnpm -r lint`
Expected: без ошибок.

- [ ] **Step 5: Коммит**

```bash
git add frontend/src/features/operator/OperatorDashboard.tsx frontend/src/tests/pages/OperatorActionsRealtime.test.tsx
git commit -m "feat(operator-ui): сворачивать подряд идущие повторы в ленте действий

Двадцать одинаковых строк читаются как двадцать разных событий и вытесняют
с экрана всё остальное. Сворачиваются только соседние одинаковые записи,
чтобы хронология не перемешивалась."
```

---

### Task 10: Брокер доступен без VPN

**Files:**
- Modify (на прод-хосте, не в репозитории): `/opt/fb-agent/shared/source.env` — значения `DESKTOP_RUSTDESK_BIND` и `DESKTOP_RUSTDESK_SERVER`
- Modify: `fbctl/config.py` (дефолт `DESKTOP_RUSTDESK_SERVER`)
- Test: `tests/unit/test_fbctl.py`

**Interfaces:**
- Consumes: `DESKTOP_RUSTDESK_SERVER` — адрес, который стол публикует оператору в `rustdesk.json` и который показывают оба фронта. `DESKTOP_RUSTDESK_BIND` — интерфейс, на котором Compose публикует порты брокеров. Оба ключа durable (`DURABLE_KEYS`), то есть переживают деплой и не ротируются.
- Produces: ничего для последующих задач.

**Решение владельца:** брокер слушал только Tailscale-адрес `100.73.162.127`, из-за чего каждое устройство требовало VPN. На Mac Tailscale не установлен вовсе, и подключение не доходило: клиент спрашивал про ID у публичного `rs-ny.rustdesk.com`, который про наш стол не знает. Владелец выбрал доступность: брокер публикуется на публичном адресе, и любое устройство подключается по адресу, ключу и ID без VPN.

**Что защищает канал после этого:** ключ брокера — клиент без него не зарегистрируется и стол не найдёт; и пароль канала на самом столе (56 символов, сгенерирован `fbctl`). Приватность держится на пароле, а не на том, что брокер спрятан. Так работает большинство self-hosted инсталляций RustDesk.

**Проверено заранее:** цепочка `DOCKER-USER` на хосте пуста, то есть опубликованные Docker'ом порты не фильтруются ufw. Правки файрвола не требуются — достаточно сменить интерфейс публикации.

- [ ] **Step 1: Написать падающий тест на дефолт адреса**

Дефолт в коде указывает на Tailscale-адрес и после этого решения вводит в заблуждение при чистой установке. В `tests/unit/test_fbctl.py` найти тест, который проверяет подстановку `DESKTOP_RUSTDESK_SERVER` по умолчанию (ищи по строке `100.73.162.127`), и заменить ожидание на публичный адрес хоста `62.60.150.133`. Если такого теста нет — добавить:

```python
def test_channel_address_defaults_to_the_public_broker() -> None:
    """Канал доступен без VPN: дефолт указывает на публичный адрес брокера.

    Приватный адрес в дефолте означал бы, что чистая установка поднимает
    стол, до которого нельзя дойти ни с одного устройства без VPN.
    """
    values = canonicalize_source(_minimal_source(), incumbent={})

    assert values["DESKTOP_RUSTDESK_SERVER"] == "62.60.150.133"
```

`_minimal_source()` — тот способ построения минимального source, который в файле уже используется соседними тестами `canonicalize_source`; возьми его оттуда.

- [ ] **Step 2: Прогнать и убедиться, что падает**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/unit/test_fbctl.py -q -k "channel_address or rustdesk_channel"`
Expected: FAIL — дефолт всё ещё `100.73.162.127`.

- [ ] **Step 3: Поменять дефолт**

В `fbctl/config.py` в `canonicalize_source` заменить подстановку по умолчанию:

```python
    result.setdefault("DESKTOP_RUSTDESK_SERVER", "62.60.150.133")
```

Рядом оставить комментарий, объясняющий выбор: адрес публичный, потому что канал должен открываться с любого устройства без VPN, а защищают его ключ брокера и пароль стола.

- [ ] **Step 4: Прогнать гейты**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/unit -q && .venv/bin/python -m ruff check . && .venv/bin/python -m ruff format --check . && ./scripts/validate-platform-configs.sh`
Expected: всё зелёное.

- [ ] **Step 5: Коммит**

```bash
git add fbctl/config.py tests/unit/test_fbctl.py
git commit -m "feat(desktop): публиковать брокер канала на публичном адресе

Брокер слушал только Tailscale-адрес, из-за чего каждое устройство
требовало VPN, а на Mac его не было вовсе — клиент спрашивал про ID у
публичного rs-ny.rustdesk.com и не находил стол. Решение владельца:
доступность важнее спрятанности. Канал защищают ключ брокера и пароль
стола, а не недостижимость адреса."
```

- [ ] **Step 6: Переключить адрес на хосте**

Значения durable и живут только на хосте, поэтому меняются там, до релиза:

```bash
ssh root@62.60.150.133 'set -e
cp -a /opt/fb-agent/shared/source.env /opt/fb-agent/shared/source.env.bak-prePublicBroker
sed -i "s/^DESKTOP_RUSTDESK_BIND=.*/DESKTOP_RUSTDESK_BIND=0.0.0.0/" /opt/fb-agent/shared/source.env
sed -i "s/^DESKTOP_RUSTDESK_SERVER=.*/DESKTOP_RUSTDESK_SERVER=62.60.150.133/" /opt/fb-agent/shared/source.env
grep -E "^DESKTOP_RUSTDESK_(BIND|SERVER)=" /opt/fb-agent/shared/source.env'
```

Expected: `DESKTOP_RUSTDESK_BIND=0.0.0.0` и `DESKTOP_RUSTDESK_SERVER=62.60.150.133`.

- [ ] **Step 7: Выкатить и проверить**

Релиз выполняется тем же способом, что и в задаче 5 (`gh workflow run release.yml --ref main` после мержа). После зелёного релиза проверить:

```bash
ssh root@62.60.150.133 'docker ps --format "{{.Names}} {{.Ports}}" | grep rustdesk; cat /opt/fb-agent/shared/desktop-readiness/rustdesk.json'
```

Expected: порты брокеров опубликованы на `0.0.0.0`, а не на `100.73.162.127`; в `rustdesk.json` поле `server` равно `62.60.150.133`, `device_id` — числовой.

Проверить доступность снаружи (с машины владельца, вне сервера):

```bash
for p in 21115 21116 21117; do nc -z -w 5 62.60.150.133 $p && echo "$p открыт" || echo "$p закрыт"; done
```

Expected: все три открыты.

Стол при этом ходит к брокеру по внутренним именам `rustdesk-id`/`rustdesk-relay` и сменой публичного адреса не затрагивается — убедиться, что после релиза он по-прежнему зарегистрирован (в логе брокера есть его ID, либо запись о нём есть в базе брокера).

- [ ] **Step 8: Убрать из UI ставшее неправдой требование VPN**

Задача 4 добавила в оба фронта подсказку «на устройстве должен быть включён Tailscale» — после этого решения она вводит оператора в заблуждение. Тесты, которые её проверяют (`expect(screen.getByText(/Tailscale/))` в обоих тест-файлах), тоже должны уйти.

Сначала поправить тесты: в `frontend/src/tests/pages/RemoteDesktop.test.tsx` и `frontend-mini/src/tests/Desktop.test.tsx` заменить проверку упоминания Tailscale на проверку, что экран НЕ требует VPN:

```tsx
  it("не требует от оператора VPN — брокер доступен напрямую", () => {
    render(<RemoteDesktopPage />);

    expect(screen.queryByText(/Tailscale/)).not.toBeInTheDocument();
    expect(screen.queryByText(/приватной сети/)).not.toBeInTheDocument();
  });
```

Тест на сжимаемость строки канала (`min-w-0` + `truncate`) не трогать — он про другое и остаётся.

Затем текст. В `frontend/src/routes/remote-desktop/index.tsx` абзац подсказки:

```tsx
                <p className="mx-auto mt-4 max-w-[460px] text-[12px] leading-5 text-bg-8">
                  Первая настройка клиента: Settings → Network → ID/Relay Server — адрес сервера и
                  ключ выше. Пароль канала приложение запомнит после первого подключения.
                </p>
```

В `frontend-mini/src/routes/desktop/index.tsx` абзац под заголовком:

```tsx
              <p className="mt-1 text-[12px] leading-relaxed text-bg-9">
                Через приложение RustDesk. Адрес и ключ вводятся один раз, пароль канала приложение
                запомнит после первого подключения.
              </p>
```

Прогнать: `cd frontend && pnpm test`, `cd frontend-mini && pnpm test`, затем `pnpm -r typecheck && pnpm -r lint`.

Коммит:

```bash
git add frontend/src/routes/remote-desktop/index.tsx frontend-mini/src/routes/desktop/index.tsx frontend/src/tests/pages/RemoteDesktop.test.tsx frontend-mini/src/tests/Desktop.test.tsx
git commit -m "fix(desktop-ui): убрать требование VPN — брокер доступен напрямую

Подсказка про Tailscale появилась, когда брокер слушал только приватный
адрес. После публикации брокера она отправляет оператора настраивать VPN,
которого не нужно."
```

---

## Порядок выполнения

Задачи 1 → 2 строго последовательны: шаг деплоя вызывает ручку из задачи 1. Задачи 3, 4 и 6 независимы и могут идти в любом порядке. Задача 5 выполняется последней и требует смерженных 1–2.

## Definition of done

- Релиз проходит зелёным без единого ручного действия между шагами.
- `/root/vision-channel-heal.sh` на прод-хосте отсутствует.
- Ни один текст в репозитории не описывает KasmVNC как часть образа.
- Оператор на странице стола видит, что для подключения нужен Tailscale.
