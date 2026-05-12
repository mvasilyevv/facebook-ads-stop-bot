# Campaign Recorder — Фаза 1A: Бэкенд

> **Для агентов:** используй superpowers:subagent-driven-development или superpowers:executing-plans.

**Цель:** Подключиться к Vision anti-detect через CDP, инжектировать JS-слушатели событий, записывать сессию в JSON, анализировать паттерны.

**Архитектура:** `core/campaign_recorder/` — 4 модуля с чёткими границами. API-роутер добавляется в существующий FastAPI. Никакого Playwright — только CDP через `playwright.async_api`.

**Стек:** Python 3.12, Playwright async CDP, FastAPI, Pydantic v2, pytest-asyncio.

---

## Файловая карта

| Действие | Файл |
|----------|------|
| Создать | `core/campaign_recorder/__init__.py` |
| Создать | `core/campaign_recorder/cdp_session.py` |
| Создать | `core/campaign_recorder/event_injector.py` |
| Создать | `core/campaign_recorder/session_writer.py` |
| Создать | `core/campaign_recorder/analyzer.py` |
| Создать | `apps/api/routers/campaign_recorder.py` |
| Изменить | `apps/api/schemas.py` |
| Изменить | `apps/api/main.py` |
| Создать | `tests/unit/test_campaign_recorder.py` |

---

### Task 1: cdp_session.py — подключение к Vision CDP

**Файлы:**
- Создать: `core/campaign_recorder/cdp_session.py`
- Тест: `tests/unit/test_campaign_recorder.py`

- [ ] **Шаг 1.1: Написать падающий тест**

```python
# tests/unit/test_campaign_recorder.py
# Проверяем что cdp_session возвращает объект сессии с нужными атрибутами
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

@pytest.mark.asyncio
async def test_cdp_session_returns_page():
    """cdp_session.connect() должен вернуть CDP-страницу Vision."""
    mock_page = MagicMock()
    mock_page.url = "https://adsmanager.facebook.com"

    with patch("core.campaign_recorder.cdp_session.async_playwright") as mock_pw:
        mock_pw.return_value.__aenter__ = AsyncMock(return_value=MagicMock(
            chromium=MagicMock(
                connect_over_cdp=AsyncMock(return_value=MagicMock(
                    contexts=[MagicMock(pages=[mock_page])]
                ))
            )
        ))
        from core.campaign_recorder.cdp_session import CdpSession
        session = CdpSession(cdp_url="http://localhost:9222")
        async with session.connect() as page:
            assert page is mock_page
```

- [ ] **Шаг 1.2: Запустить тест, убедиться что падает**

```bash
pytest tests/unit/test_campaign_recorder.py::test_cdp_session_returns_page -v
```
Ожидаем: `ModuleNotFoundError: No module named 'core.campaign_recorder'`

- [ ] **Шаг 1.3: Создать `core/campaign_recorder/__init__.py`**

```python
# -*- coding: utf-8 -*-
"""Модуль записи действий пользователя в Ads Manager через CDP."""
```

- [ ] **Шаг 1.4: Создать `core/campaign_recorder/cdp_session.py`**

```python
# -*- coding: utf-8 -*-
"""CDP-подключение к Vision anti-detect браузеру."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from playwright.async_api import Page, async_playwright

logger = logging.getLogger(__name__)


class CdpConnectionError(RuntimeError):
    """Не удалось подключиться к CDP Vision."""


class CdpSession:
    """Подключение к уже запущенному Vision-профилю через CDP."""

    def __init__(self, cdp_url: str) -> None:
        self._cdp_url = cdp_url

    @asynccontextmanager
    async def connect(self) -> AsyncGenerator[Page, None]:
        """Подключается через CDP и возвращает активную страницу."""
        async with async_playwright() as pw:
            try:
                browser = await pw.chromium.connect_over_cdp(self._cdp_url)
            except Exception as exc:
                raise CdpConnectionError(
                    f"Не удалось подключиться к CDP по адресу {self._cdp_url}: {exc}"
                ) from exc

            contexts = browser.contexts
            if not contexts:
                raise CdpConnectionError("CDP подключён, но нет открытых контекстов")

            pages = contexts[0].pages
            if not pages:
                raise CdpConnectionError("Контекст есть, но нет открытых вкладок")

            page = pages[0]
            logger.info("CDP подключён: %s", page.url)
            try:
                yield page
            finally:
                await browser.close()
```

- [ ] **Шаг 1.5: Запустить тест, убедиться что проходит**

```bash
pytest tests/unit/test_campaign_recorder.py::test_cdp_session_returns_page -v
```
Ожидаем: `PASSED`

- [ ] **Шаг 1.6: Коммит**

```bash
git add core/campaign_recorder/ tests/unit/test_campaign_recorder.py
git commit -m "feat: campaign_recorder — CDP-сессия к Vision"
```

---

### Task 2: event_injector.py — JS-инжект слушателей

**Файлы:**
- Создать: `core/campaign_recorder/event_injector.py`
- Тест: `tests/unit/test_campaign_recorder.py`

- [ ] **Шаг 2.1: Написать падающий тест**

```python
# Проверяем что JS-сниппет содержит обработчики нужных событий
def test_injector_js_contains_event_listeners():
    """JS-сниппет должен слушать click, input, change, select, focus."""
    from core.campaign_recorder.event_injector import BUILD_JS_INJECTOR
    js = BUILD_JS_INJECTOR()
    for event in ["click", "input", "change", "select", "focus"]:
        assert event in js, f"JS не содержит обработчик события {event}"
```

```python
# Проверяем что evaluate_injector вызывает add_script_tag с нашим JS
@pytest.mark.asyncio
async def test_injector_injects_into_page():
    """inject_event_listener должен вызвать evaluate на странице."""
    mock_page = AsyncMock()
    from core.campaign_recorder.event_injector import inject_event_listener
    await inject_event_listener(mock_page)
    mock_page.evaluate.assert_called_once()
```

- [ ] **Шаг 2.2: Запустить тесты, убедиться что падают**

```bash
pytest tests/unit/test_campaign_recorder.py::test_injector_js_contains_event_listeners tests/unit/test_campaign_recorder.py::test_injector_injects_into_page -v
```
Ожидаем: `ImportError`

- [ ] **Шаг 2.3: Создать `core/campaign_recorder/event_injector.py`**

```python
# -*- coding: utf-8 -*-
"""Инжект JS-слушателей событий в страницу через CDP."""

from __future__ import annotations

import json
import logging

from playwright.async_api import Page

logger = logging.getLogger(__name__)

# Максимальная длина текста элемента — обрезаем чтобы не писать огромные блоки
_MAX_TEXT_LEN = 200


def BUILD_JS_INJECTOR() -> str:
    """Возвращает JS-сниппет, который собирает события DOM."""
    return f"""
(function() {{
  if (window.__fbRecorder) return;
  window.__fbRecorder = {{ events: [] }};

  function getXPath(el) {{
    if (!el) return '';
    if (el === document.body) return '/html/body';
    const idx = Array.from(el.parentNode.children).indexOf(el) + 1;
    return getXPath(el.parentNode) + '/' + el.tagName.toLowerCase() + '[' + idx + ']';
  }}

  function getDataAttrs(el) {{
    const result = {{}};
    Array.from(el.attributes).forEach(function(attr) {{
      if (attr.name.startsWith('data-')) result[attr.name] = attr.value;
    }});
    return result;
  }}

  function getText(el) {{
    const t = (el.innerText || el.textContent || '').trim();
    return t.length > {_MAX_TEXT_LEN} ? t.slice(0, {_MAX_TEXT_LEN}) : t;
  }}

  function record(type, el, value) {{
    const ev = {{
      ts: Date.now() / 1000,
      type: type,
      tag: el.tagName ? el.tagName.toLowerCase() : '',
      id: el.id || '',
      classes: Array.from(el.classList || []),
      data_attrs: getDataAttrs(el),
      xpath: getXPath(el),
      text: getText(el),
      value: value !== undefined ? value : null,
      x: el.getBoundingClientRect ? Math.round(el.getBoundingClientRect().x) : 0,
      y: el.getBoundingClientRect ? Math.round(el.getBoundingClientRect().y) : 0,
      role: el.getAttribute ? el.getAttribute('role') : null,
      aria_label: el.getAttribute ? el.getAttribute('aria-label') : null
    }};
    window.__fbRecorder.events.push(ev);
  }}

  document.addEventListener('click', function(e) {{ record('click', e.target); }}, true);
  document.addEventListener('input', function(e) {{ record('input', e.target, e.target.value); }}, true);
  document.addEventListener('change', function(e) {{ record('change', e.target, e.target.value); }}, true);
  document.addEventListener('select', function(e) {{ record('select', e.target, e.target.value); }}, true);
  document.addEventListener('focus', function(e) {{ record('focus', e.target); }}, true);
}})();
"""


async def inject_event_listener(page: Page) -> None:
    """Инжектирует JS-слушатели в текущую страницу."""
    js = BUILD_JS_INJECTOR()
    await page.evaluate(js)
    logger.info("JS-слушатели инжектированы в страницу")


async def collect_events(page: Page) -> list[dict]:
    """Собирает накопленные события из window.__fbRecorder."""
    result = await page.evaluate("() => window.__fbRecorder ? window.__fbRecorder.events : []")
    return result if isinstance(result, list) else []


async def clear_events(page: Page) -> None:
    """Сбрасывает накопленные события."""
    await page.evaluate("() => { if (window.__fbRecorder) window.__fbRecorder.events = []; }")
```

- [ ] **Шаг 2.4: Запустить тесты, убедиться что проходят**

```bash
pytest tests/unit/test_campaign_recorder.py::test_injector_js_contains_event_listeners tests/unit/test_campaign_recorder.py::test_injector_injects_into_page -v
```
Ожидаем: `2 passed`

- [ ] **Шаг 2.5: Коммит**

```bash
git add core/campaign_recorder/event_injector.py
git commit -m "feat: campaign_recorder — JS-инжект слушателей событий"
```

---

### Task 3: session_writer.py — запись событий в JSON

**Файлы:**
- Создать: `core/campaign_recorder/session_writer.py`
- Тест: `tests/unit/test_campaign_recorder.py`

- [ ] **Шаг 3.1: Написать падающие тесты**

```python
# Проверяем создание файла записи и сохранение событий
import json
import tempfile
from pathlib import Path

def test_session_writer_creates_file():
    """SessionWriter должен создать JSON-файл при закрытии."""
    from core.campaign_recorder.session_writer import SessionWriter
    with tempfile.TemporaryDirectory() as tmpdir:
        writer = SessionWriter(offer_code="DRC_CR2", recordings_dir=Path(tmpdir))
        writer.add_events([{"type": "click", "ts": 1.0, "tag": "button"}])
        path = writer.save()
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["offer_code"] == "DRC_CR2"
        assert len(data["events"]) == 1

def test_session_writer_filename_contains_offer():
    """Имя файла должно содержать код оффера."""
    from core.campaign_recorder.session_writer import SessionWriter
    with tempfile.TemporaryDirectory() as tmpdir:
        writer = SessionWriter(offer_code="DRC_CR2", recordings_dir=Path(tmpdir))
        writer.add_events([])
        path = writer.save()
        assert "DRC_CR2" in path.name
```

- [ ] **Шаг 3.2: Запустить тесты, убедиться что падают**

```bash
pytest tests/unit/test_campaign_recorder.py::test_session_writer_creates_file tests/unit/test_campaign_recorder.py::test_session_writer_filename_contains_offer -v
```
Ожидаем: `ImportError`

- [ ] **Шаг 3.3: Создать `core/campaign_recorder/session_writer.py`**

```python
# -*- coding: utf-8 -*-
"""Сохранение записанной сессии в JSON-файл."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_RECORDINGS_DIR = Path("recordings")


class SessionWriter:
    """Накапливает события сессии и записывает в JSON."""

    def __init__(self, offer_code: str, recordings_dir: Path | None = None) -> None:
        self._offer_code = offer_code.upper().strip()
        self._dir = (recordings_dir or _DEFAULT_RECORDINGS_DIR).expanduser().resolve()
        self._events: list[dict] = []
        self._started_at = datetime.now(UTC)

    def add_events(self, events: list[dict]) -> None:
        """Добавляет пачку событий в буфер."""
        self._events.extend(events)

    def save(self) -> Path:
        """Записывает сессию в файл и возвращает путь."""
        self._dir.mkdir(parents=True, exist_ok=True)
        ts = self._started_at.strftime("%Y%m%d_%H%M%S")
        filename = f"{ts}_{self._offer_code}.json"
        path = self._dir / filename
        payload = {
            "offer_code": self._offer_code,
            "started_at": self._started_at.isoformat(),
            "saved_at": datetime.now(UTC).isoformat(),
            "event_count": len(self._events),
            "events": self._events,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        logger.info("Сессия сохранена: %s (%d событий)", path, len(self._events))
        return path
```

- [ ] **Шаг 3.4: Запустить тесты, убедиться что проходят**

```bash
pytest tests/unit/test_campaign_recorder.py::test_session_writer_creates_file tests/unit/test_campaign_recorder.py::test_session_writer_filename_contains_offer -v
```
Ожидаем: `2 passed`

- [ ] **Шаг 3.5: Коммит**

```bash
git add core/campaign_recorder/session_writer.py
git commit -m "feat: campaign_recorder — запись сессии в JSON"
```

---

### Task 4: analyzer.py — анализ паттернов из записи

**Файлы:**
- Создать: `core/campaign_recorder/analyzer.py`
- Тест: `tests/unit/test_campaign_recorder.py`

- [ ] **Шаг 4.1: Написать падающий тест**

```python
# Проверяем что анализатор считает повторяющиеся элементы и типы событий
def test_analyzer_counts_event_types():
    """Анализатор должен подсчитать количество каждого типа событий."""
    from core.campaign_recorder.analyzer import analyze_session
    events = [
        {"type": "click", "tag": "button", "text": "Создать", "classes": ["btn"], "data_attrs": {}, "id": "", "role": None, "aria_label": None, "xpath": "//button[1]", "value": None},
        {"type": "click", "tag": "div", "text": "Конверсии", "classes": [], "data_attrs": {}, "id": "", "role": "option", "aria_label": None, "xpath": "//div[2]", "value": None},
        {"type": "input", "tag": "input", "text": "", "classes": [], "data_attrs": {}, "id": "campaign_name", "role": None, "aria_label": "Название кампании", "xpath": "//input[1]", "value": "MV | DRC"},
    ]
    report = analyze_session({"offer_code": "DRC_CR2", "events": events})
    assert report["total_events"] == 3
    assert report["by_type"]["click"] == 2
    assert report["by_type"]["input"] == 1
    assert len(report["stable_selectors"]) > 0

def test_analyzer_detects_stable_selectors():
    """Элементы с aria-label или id считаются стабильными селекторами."""
    from core.campaign_recorder.analyzer import analyze_session
    events = [
        {"type": "input", "tag": "input", "id": "campaign_name", "aria_label": "Название", "classes": [], "data_attrs": {}, "text": "", "role": None, "xpath": "//input[1]", "value": "test"},
        {"type": "click", "tag": "button", "id": "", "aria_label": None, "classes": ["_abc123"], "data_attrs": {}, "text": "", "role": None, "xpath": "//button[1]", "value": None},
    ]
    report = analyze_session({"offer_code": "DRC_CR2", "events": events})
    stable = {s["selector"] for s in report["stable_selectors"]}
    # input с id — стабильный
    assert any("campaign_name" in s for s in stable)
```

- [ ] **Шаг 4.2: Запустить тесты, убедиться что падают**

```bash
pytest tests/unit/test_campaign_recorder.py::test_analyzer_counts_event_types tests/unit/test_campaign_recorder.py::test_analyzer_detects_stable_selectors -v
```

- [ ] **Шаг 4.3: Создать `core/campaign_recorder/analyzer.py`**

```python
# -*- coding: utf-8 -*-
"""Анализ записанной сессии: паттерны, повторы, надёжность селекторов."""

from __future__ import annotations

import json
import logging
from collections import Counter
from pathlib import Path

logger = logging.getLogger(__name__)


def _selector_for_event(event: dict) -> str | None:
    """Возвращает наиболее надёжный CSS-селектор для элемента."""
    if event.get("id"):
        return f"#{event['id']}"
    if event.get("aria_label"):
        return f"[aria-label=\"{event['aria_label']}\"]"
    data = event.get("data_attrs", {})
    if data:
        key, val = next(iter(data.items()))
        return f"[{key}=\"{val}\"]"
    return None


def _is_stable(event: dict) -> bool:
    """Элемент считается стабильным если есть id, aria-label или data-атрибуты."""
    return bool(event.get("id") or event.get("aria_label") or event.get("data_attrs"))


def analyze_session(session: dict) -> dict:
    """Анализирует сессию и возвращает отчёт с паттернами.

    session — dict с ключами offer_code и events (из JSON-файла).
    """
    events: list[dict] = session.get("events", [])
    by_type: Counter = Counter(e.get("type") for e in events)

    stable: list[dict] = []
    fragile: list[dict] = []
    for event in events:
        selector = _selector_for_event(event)
        entry = {
            "selector": selector or event.get("xpath", ""),
            "type": event.get("type"),
            "tag": event.get("tag"),
            "text": event.get("text", "")[:80],
            "value": event.get("value"),
            "is_stable": _is_stable(event),
        }
        if _is_stable(event) and selector:
            stable.append(entry)
        else:
            fragile.append(entry)

    # Шаги — последовательность событий типа click с текстом
    steps = [
        {"step": i + 1, "type": e.get("type"), "text": (e.get("text") or "")[:60], "value": e.get("value")}
        for i, e in enumerate(events)
        if e.get("type") in ("click", "input", "change")
    ]

    return {
        "offer_code": session.get("offer_code", ""),
        "total_events": len(events),
        "by_type": dict(by_type),
        "stable_selectors": stable,
        "fragile_selectors": fragile,
        "steps_summary": steps,
        "recommendations": _build_recommendations(stable, fragile),
    }


def _build_recommendations(stable: list, fragile: list) -> list[str]:
    """Формирует список рекомендаций по надёжности автоматизации."""
    recs = []
    if fragile:
        recs.append(
            f"{len(fragile)} элементов без стабильного селектора — "
            "возможны проблемы при автоматизации. Используй aria-label или data-атрибуты."
        )
    if stable:
        recs.append(f"{len(stable)} элементов имеют надёжные селекторы — готовы к автоматизации.")
    if not recs:
        recs.append("Недостаточно данных для анализа.")
    return recs


def analyze_session_file(path: Path) -> dict:
    """Загружает JSON-файл сессии и возвращает отчёт."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return analyze_session(data)
```

- [ ] **Шаг 4.4: Запустить тесты, убедиться что проходят**

```bash
pytest tests/unit/test_campaign_recorder.py::test_analyzer_counts_event_types tests/unit/test_campaign_recorder.py::test_analyzer_detects_stable_selectors -v
```
Ожидаем: `2 passed`

- [ ] **Шаг 4.5: Коммит**

```bash
git add core/campaign_recorder/analyzer.py
git commit -m "feat: campaign_recorder — анализатор паттернов из записи"
```

---

### Task 5: API роутер — управление записью

**Файлы:**
- Создать: `apps/api/routers/campaign_recorder.py`
- Изменить: `apps/api/schemas.py` (добавить схемы)
- Изменить: `apps/api/main.py` (подключить роутер)

- [ ] **Шаг 5.1: Добавить схемы в `apps/api/schemas.py`**

В конец файла добавить:

```python
# --- Campaign Recorder ---

class RecorderStartRequestSchema(BaseModel):
    """Запрос на старт записи сессии."""
    offer_code: str
    cdp_url: str  # CDP-адрес Vision-профиля, например ws://localhost:9222


class RecorderStartResponseSchema(BaseModel):
    """Ответ после старта записи."""
    session_id: str
    started: bool


class RecorderStopResponseSchema(BaseModel):
    """Ответ после остановки записи."""
    session_id: str
    event_count: int
    file_path: str


class RecorderAnalyzeResponseSchema(BaseModel):
    """Отчёт анализатора по последней сессии."""
    offer_code: str
    total_events: int
    by_type: dict[str, int]
    stable_selectors: list[dict]
    fragile_selectors: list[dict]
    steps_summary: list[dict]
    recommendations: list[str]
```

- [ ] **Шаг 5.2: Создать `apps/api/routers/campaign_recorder.py`**

```python
# -*- coding: utf-8 -*-
"""FastAPI роутер для управления записью сессий создания кампаний."""

from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException

from apps.api.schemas import (
    RecorderAnalyzeResponseSchema,
    RecorderStartRequestSchema,
    RecorderStartResponseSchema,
    RecorderStopResponseSchema,
)
from core.campaign_recorder.analyzer import analyze_session_file
from core.campaign_recorder.cdp_session import CdpConnectionError, CdpSession
from core.campaign_recorder.event_injector import collect_events, inject_event_listener
from core.campaign_recorder.session_writer import SessionWriter

router = APIRouter(prefix="/api/campaign-recorder", tags=["campaign-recorder"])

logger = logging.getLogger(__name__)

# Активные сессии: session_id → (writer, page, browser_task)
_active_sessions: dict[str, dict] = {}


@router.post("/start", response_model=RecorderStartResponseSchema)
async def start_recording(body: RecorderStartRequestSchema):
    """Подключиться к Vision CDP и начать запись событий."""
    session_id = str(uuid.uuid4())
    writer = SessionWriter(offer_code=body.offer_code)

    # Запускаем CDP-подключение и инжект в фоне
    async def _run_session():
        session = CdpSession(cdp_url=body.cdp_url)
        try:
            async with session.connect() as page:
                await inject_event_listener(page)
                _active_sessions[session_id]["page"] = page
                _active_sessions[session_id]["status"] = "recording"
                # Ждём сигнала остановки
                stop_event: asyncio.Event = _active_sessions[session_id]["stop_event"]
                while not stop_event.is_set():
                    await asyncio.sleep(2)
                    events = await collect_events(page)
                    if events:
                        writer.add_events(events)
        except CdpConnectionError as exc:
            logger.error("Ошибка CDP: %s", exc)
            _active_sessions[session_id]["status"] = "error"
            _active_sessions[session_id]["error"] = str(exc)

    stop_event = asyncio.Event()
    _active_sessions[session_id] = {
        "writer": writer,
        "page": None,
        "stop_event": stop_event,
        "status": "connecting",
        "error": None,
    }
    task = asyncio.create_task(_run_session())
    _active_sessions[session_id]["task"] = task

    return RecorderStartResponseSchema(session_id=session_id, started=True)


@router.post("/stop/{session_id}", response_model=RecorderStopResponseSchema)
async def stop_recording(session_id: str):
    """Остановить запись и сохранить JSON-файл."""
    entry = _active_sessions.get(session_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Сессия записи не найдена")

    entry["stop_event"].set()
    # Ждём завершения задачи (макс 5 сек)
    try:
        await asyncio.wait_for(entry["task"], timeout=5.0)
    except asyncio.TimeoutError:
        entry["task"].cancel()

    writer: SessionWriter = entry["writer"]
    path = writer.save()
    event_count = writer._events.__len__()
    _active_sessions.pop(session_id, None)

    return RecorderStopResponseSchema(
        session_id=session_id,
        event_count=event_count,
        file_path=str(path),
    )


@router.get("/analyze", response_model=RecorderAnalyzeResponseSchema)
async def analyze_last_recording(offer_code: str | None = None):
    """Проанализировать последний JSON-файл записи."""
    recordings_dir = Path("recordings")
    if not recordings_dir.exists():
        raise HTTPException(status_code=404, detail="Папка recordings не найдена")

    files = sorted(
        [f for f in recordings_dir.glob("*.json") if (not offer_code or offer_code.upper() in f.name.upper())],
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )
    if not files:
        raise HTTPException(status_code=404, detail="Нет файлов записи")

    report = analyze_session_file(files[0])
    return RecorderAnalyzeResponseSchema(**report)
```

- [ ] **Шаг 5.3: Подключить роутер в `apps/api/main.py`**

Найти блок с `include_router` (там уже есть `campaign_scripts`, `creative_tools` и другие) и добавить:

```python
from apps.api.routers.campaign_recorder import router as campaign_recorder_router
# ...
app.include_router(campaign_recorder_router)
```

- [ ] **Шаг 5.4: Проверить что API стартует без ошибок**

```bash
python -c "from apps.api.main import app; print('OK')"
```
Ожидаем: `OK`

- [ ] **Шаг 5.5: Коммит**

```bash
git add apps/api/routers/campaign_recorder.py apps/api/schemas.py apps/api/main.py
git commit -m "feat: campaign_recorder — API роутер start/stop/analyze"
```

---

### Task 6: Финальный прогон всех тестов

- [ ] **Шаг 6.1: Запустить все новые тесты**

```bash
pytest tests/unit/test_campaign_recorder.py -v
```
Ожидаем: все `PASSED`

- [ ] **Шаг 6.2: Убедиться что старые тесты не сломались**

```bash
pytest tests/unit/ -v --tb=short -q
```
Ожидаем: все `PASSED`

- [ ] **Шаг 6.3: Линтер**

```bash
ruff check core/campaign_recorder/ apps/api/routers/campaign_recorder.py
```
Ожидаем: без ошибок

- [ ] **Шаг 6.4: Итоговый коммит**

```bash
git add .
git commit -m "feat: campaign_recorder фаза 1A — бэкенд готов"
```
