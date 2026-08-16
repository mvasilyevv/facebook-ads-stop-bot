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

### Task 4: Оператор видит, что канал живёт в приватной сети

**Files:**
- Modify: `frontend/src/routes/remote-desktop/index.tsx:106-109`
- Modify: `frontend-mini/src/routes/desktop/index.tsx:52-55`
- Test: `frontend/src/tests/pages/RemoteDesktop.test.tsx`, `frontend-mini/src/tests/Desktop.test.tsx`

**Interfaces:**
- Consumes: `useDesktopNativeChannel()` из `frontend/src/lib/api/desktop.ts` и инлайновый `tmaApi.useQuery("get", "/api/desktop/native", ...)` в мини-аппе — оба отдают `{ available, server, key, device_id }`.
- Produces: ничего для последующих задач.

**Почему:** брокер слушает только приватный адрес, поэтому с устройства без Tailscale подключение не состоится вообще — и без подсказки это выглядит как поломка стола, а не как отсутствующий VPN. Текущая формулировка «Сервер доступен только из приватной сети» верна, но не говорит, что именно включить.

- [ ] **Step 1: Написать падающий тест веб-страницы**

В `frontend/src/tests/pages/RemoteDesktop.test.tsx` добавить внутрь `describe("RemoteDesktopPage", ...)`:

```tsx
  it("называет условие доступа, а не только факт приватной сети", () => {
    render(<RemoteDesktopPage />);

    expect(screen.getByText(/Tailscale/)).toBeInTheDocument();
  });
```

- [ ] **Step 2: Написать падающий тест мини-аппа**

В `frontend-mini/src/tests/Desktop.test.tsx` добавить внутрь `describe("Mini App RemoteDesktopPage", ...)`:

```tsx
  it("называет условие доступа, а не только факт приватной сети", () => {
    render(<RemoteDesktopPage />);

    expect(screen.getByText(/Tailscale/)).toBeInTheDocument();
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

- [ ] **Step 6: Прогнать тесты обоих фронтов**

Run: `cd frontend && pnpm test`
Expected: PASS — `Test Files 55 passed`, `Tests 502 passed` (на один больше прежнего).

Run: `cd frontend-mini && pnpm test`
Expected: PASS — `Test Files 32 passed`, `Tests 185 passed`.

- [ ] **Step 7: Проверить типы, линт и типографский гард**

Run: `pnpm -r typecheck && pnpm -r lint`
Expected: обе команды завершаются `Done` без ошибок. Гард типографики требует размера шрифта не меньше 12px — в правках использованы `text-[12px]`.

- [ ] **Step 8: Коммит**

```bash
git add frontend/src/routes/remote-desktop/index.tsx frontend-mini/src/routes/desktop/index.tsx frontend/src/tests/pages/RemoteDesktop.test.tsx frontend-mini/src/tests/Desktop.test.tsx
git commit -m "fix(desktop-ui): назвать условие доступа к столу, а не только приватную сеть

Брокер слушает только приватный адрес, и с устройства без Tailscale
подключение не доходит вовсе. Без явной подсказки это читается как
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

## Порядок выполнения

Задачи 1 → 2 строго последовательны: шаг деплоя вызывает ручку из задачи 1. Задачи 3 и 4 независимы и могут идти параллельно с 1–2 (разные файлы, пересечений нет). Задача 5 выполняется последней и требует смерженных 1–2.

## Definition of done

- Релиз проходит зелёным без единого ручного действия между шагами.
- `/root/vision-channel-heal.sh` на прод-хосте отсутствует.
- Ни один текст в репозитории не описывает KasmVNC как часть образа.
- Оператор на странице стола видит, что для подключения нужен Tailscale.
