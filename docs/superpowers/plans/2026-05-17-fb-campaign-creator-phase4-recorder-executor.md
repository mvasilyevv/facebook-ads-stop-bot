# Phase 4 — Recorder + Executor wiring

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) или `superpowers:executing-plans`.

**Goal:** Дописать `recorder.ts` (запись действий пользователя как намерений), CLI `apps/creator_recorder`, воркер `apps/creator_worker` (Vision → CDP → page.evaluate). После этой фазы можно реально записать план и реально запустить его на тестовом аккаунте.

**Architecture:** Recorder подписывается capture-фазой на `click`/`change`/`input` (input debounce 800мс). Для каждого события вызывает `step.match(ev, dom)` у каждого зарегистрированного шага → отправляет в Python через `fbAgentEmit('recorded_step', {...})`. Python складывает в `Plan.steps`. Executor работает поверх существующего `CreatorRunner` (Phase 1) + использует `core/browser/manager.py` для Vision-профиля. Реагирует на `request_upload` (Phase 3 upload_creatives) через `page.locator(selector).setInputFiles(...)`.

**Tech Stack:** TypeScript 5.7, Python 3.12, Playwright async, SQLAlchemy 2.x.

---

## File Structure

- Create: `services/browser-agent/src/creator/recorder.ts`
- Create: `services/browser-agent/src/creator/recorder.test.ts`
- Modify: `services/browser-agent/src/creator/index.ts` — wire `startRecording`/`stopRecording`.
- Create: `apps/creator_recorder/__init__.py`
- Create: `apps/creator_recorder/main.py` — CLI `python -m apps.creator_recorder --profile=... --name=...`.
- Create: `tests/unit/test_creator_recorder_cli.py`
- Create: `apps/creator_worker/__init__.py`
- Create: `apps/creator_worker/main.py` — entrypoint цикла.
- Create: `apps/creator_worker/runner.py` — `CreatorWorkerRunner` (Vision → CDP → CreatorRunner.run_plan).
- Create: `tests/unit/test_creator_worker_runner.py`
- Create: `run_creator_worker.py` — корневой entrypoint (по образцу `run_observer.py`).

---

### Task 1: `recorder.ts` — capture click/input/change

- [ ] **Step 1: Failing test** `creator/recorder.test.ts`:

```typescript
import { describe, it } from 'node:test';
import assert from 'node:assert';
import { Recorder } from './recorder.js';
import { registerStep, clearRegistry } from './registry.js';
import { BaseStep } from './steps/base.js';
import type { StepState } from './types.js';

class Stub extends BaseStep<{ x: number }, void> {
  name = 'stub';
  match(ev: any): boolean { return ev.selector === '.stub'; }
  detect(): StepState { return { kind: 'unknown' }; }
  isSatisfied(): boolean { return false; }
  protected async run(): Promise<void> {}
}

describe('Recorder', () => {
  it('эмитит recorded_step когда step.match совпал', async () => {
    clearRegistry();
    registerStep(new Stub());
    const events: any[] = [];
    const rec = new Recorder((e, p) => events.push([e, p]));
    rec.start('test plan');
    const btn = document.createElement('button');
    btn.className = 'stub';
    document.body.appendChild(btn);
    btn.click();
    await new Promise((r) => setTimeout(r, 50));
    rec.stop();
    const recorded = events.find(([e]) => e === 'recorded_step');
    assert.ok(recorded, 'recorded_step должен быть эмитен');
    assert.equal(recorded[1].step, 'stub');
  });

  it('эмитит unknown когда ни один step.match не совпал', async () => {
    clearRegistry();
    const events: any[] = [];
    const rec = new Recorder((e, p) => events.push([e, p]));
    rec.start('test');
    const btn = document.createElement('button');
    btn.className = 'xx';
    document.body.appendChild(btn);
    btn.click();
    await new Promise((r) => setTimeout(r, 50));
    rec.stop();
    const unknown = events.find(([e]) => e === 'recorded_unknown');
    assert.ok(unknown);
  });
});
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement** `creator/recorder.ts`:

```typescript
import { listSteps } from './registry.js';
import type { RecordedEvent, DomState } from './types.js';

export type RecorderEmit = (event: string, payload?: unknown) => void;

function buildSelector(el: Element): string {
  if (el.id) return `#${CSS.escape(el.id)}`;
  const testid = el.getAttribute('data-testid');
  if (testid) return `[data-testid="${testid}"]`;
  const cls = (el.className || '').toString().trim().split(/\s+/).slice(0, 2).filter(Boolean);
  return `${el.tagName.toLowerCase()}${cls.length ? '.' + cls.join('.') : ''}`;
}

function makeRecordedEvent(type: RecordedEvent['type'], target: Element): RecordedEvent {
  const value = (target as HTMLInputElement).value ?? null;
  return {
    type,
    selector: buildSelector(target),
    text: (target.textContent || '').trim().slice(0, 200),
    value,
  };
}

export class Recorder {
  private active = false;
  private inputTimers = new WeakMap<Element, number>();
  private emit: RecorderEmit;

  constructor(emit: RecorderEmit) {
    this.emit = emit;
  }

  start(planName: string): void {
    this.active = true;
    this.emit('recording_started', { planName });
    document.addEventListener('click', this.onClick, true);
    document.addEventListener('change', this.onChange, true);
    document.addEventListener('input', this.onInput, true);
  }

  stop(): void {
    this.active = false;
    document.removeEventListener('click', this.onClick, true);
    document.removeEventListener('change', this.onChange, true);
    document.removeEventListener('input', this.onInput, true);
    this.emit('recording_stopped');
  }

  private dispatch(ev: RecordedEvent): void {
    const dom: DomState = { url: location.href, title: document.title };
    for (const step of listSteps()) {
      if (step.match?.(ev, dom)) {
        this.emit('recorded_step', { step: step.name, raw: ev });
        return;
      }
    }
    this.emit('recorded_unknown', { raw: ev });
  }

  private onClick = (e: Event) => {
    if (!this.active || !(e.target instanceof Element)) return;
    this.dispatch(makeRecordedEvent('click', e.target));
  };

  private onChange = (e: Event) => {
    if (!this.active || !(e.target instanceof Element)) return;
    this.dispatch(makeRecordedEvent('change', e.target));
  };

  private onInput = (e: Event) => {
    if (!this.active || !(e.target instanceof Element)) return;
    const target = e.target;
    const prev = this.inputTimers.get(target);
    if (prev) clearTimeout(prev);
    const timer = window.setTimeout(() => this.dispatch(makeRecordedEvent('input', target)), 800);
    this.inputTimers.set(target, timer);
  };
}
```

- [ ] **Step 4: Run** → PASS.

- [ ] **Step 5: Commit**

```bash
git add services/browser-agent/src/creator/recorder*.ts
git commit -m "feat(creator): recorder с capture-фазой и step.match"
```

---

### Task 2: Wire recorder в `creator/index.ts`

- [ ] **Step 1: Failing test** — обновить `index.test.ts`:

```typescript
it('startRecording/stopRecording создают/закрывают Recorder', async () => {
  const win: any = (globalThis as any).window;
  await win.__fbAgent.startRecording('plan1');
  await win.__fbAgent.stopRecording();
  // должно не падать
});
```

- [ ] **Step 2: Run** → FAIL (текущая реализация бросает).

- [ ] **Step 3: Implement** в `creator/index.ts`:

```typescript
import { Recorder } from './recorder.js';

let _activeRecorder: Recorder | null = null;

const api = {
  // ... run() как было
  async startRecording(planName: string): Promise<void> {
    if (_activeRecorder) throw new Error('Recorder уже запущен');
    const emit = (event: string, payload?: unknown) => {
      const fn = (globalThis as any).fbAgentEmit;
      if (typeof fn === 'function') fn(event, payload);
    };
    _activeRecorder = new Recorder(emit);
    _activeRecorder.start(planName);
  },
  async stopRecording(): Promise<void> {
    _activeRecorder?.stop();
    _activeRecorder = null;
  },
};
```

- [ ] **Step 4: Run** → PASS.

- [ ] **Step 5: Commit** `feat(creator): wire recorder lifecycle into window.__fbAgent`.

---

### Task 3: Python CLI `apps/creator_recorder`

- [ ] **Step 1: Failing test** `tests/unit/test_creator_recorder_cli.py`:

```python
# Проверяем что CLI парсит --profile и --name, и что записанные события складываются в Plan.steps.
import asyncio
from unittest.mock import AsyncMock, MagicMock

from apps.creator_recorder.main import RecorderSession


def test_recorder_session_collects_steps():
    session = RecorderSession(profile_id="p1", plan_name="t1")
    session.on_emit("recorded_step", {"step": "set_geo", "raw": {"selector": ".x", "text": "DE"}})
    session.on_emit("recorded_unknown", {"raw": {"selector": ".y"}})
    plan = session.build_plan()
    assert plan["name"] == "t1"
    assert plan["steps"][0]["step"] == "set_geo"
    assert plan["steps"][1]["step"] == "unknown"
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement** `apps/creator_recorder/main.py`:

```python
"""CLI рекордер: python -m apps.creator_recorder --profile=<id> --name=...

Поднимает Vision-профиль, цепляется через CDP, инжектит creator-бандл,
запускает window.__fbAgent.startRecording, складывает recorded_* в список,
по Ctrl+C завершает и сохраняет Plan в БД.
"""

from __future__ import annotations

import argparse
import asyncio
import signal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from core.browser.manager import VisionBrowserManager
from core.creator_bridge.bundle import load_bundle
from core.creator_bridge.runner import CreatorRunner
from core.db import session_factory
from core.models import Plan


class RecorderSession:
    """Хранит recorded_* события и собирает Plan."""

    def __init__(self, profile_id: str, plan_name: str) -> None:
        self.profile_id = profile_id
        self.plan_name = plan_name
        self._events: list[dict[str, Any]] = []

    def on_emit(self, event: str, payload: Any) -> None:
        if event == "recorded_step":
            self._events.append({"step": payload["step"], "input": payload.get("raw", {})})
        elif event == "recorded_unknown":
            self._events.append({"step": "unknown", "input": {"raw": payload.get("raw")}})

    def build_plan(self) -> dict[str, Any]:
        return {
            "name": self.plan_name,
            "schema_version": 1,
            "steps": self._events,
        }


async def _save_plan(plan: dict[str, Any]) -> None:
    async with session_factory() as s:  # type: AsyncSession
        s.add(Plan(name=plan["name"], schema_version=plan["schema_version"], steps=plan["steps"]))
        await s.commit()


async def _run(profile_id: str, plan_name: str) -> None:
    session = RecorderSession(profile_id, plan_name)
    bundle = load_bundle()
    async with VisionBrowserManager(profile_id) as page:
        runner = CreatorRunner(page, bundle)
        await runner.attach(on_emit=session.on_emit)
        await page.evaluate(f"window.__fbAgent.startRecording({plan_name!r})")
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
        print(f"Рекордер запущен для плана '{plan_name}'. Создавай кампанию вручную. Ctrl+C — завершить.")
        await stop.wait()
        await page.evaluate("window.__fbAgent.stopRecording()")
    plan = session.build_plan()
    await _save_plan(plan)
    print(f"Сохранён план '{plan_name}' с {len(plan['steps'])} шагами.")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--profile", required=True)
    p.add_argument("--name", required=True)
    args = p.parse_args()
    asyncio.run(_run(args.profile, args.name))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run unit test** → PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/creator_recorder/ tests/unit/test_creator_recorder_cli.py
git commit -m "feat(creator): CLI recorder (apps.creator_recorder)"
```

---

### Task 4: `apps/creator_worker/runner.py` — `CreatorWorkerRunner`

- [ ] **Step 1: Failing test** `tests/unit/test_creator_worker_runner.py`:

```python
# Воркер должен поднимать профиль, инжектить бандл, гонять run_plan и обновлять PlanRun.
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from apps.creator_worker.runner import CreatorWorkerRunner
from core.domain import PlanRunStatus


def test_runner_marks_run_success():
    page = MagicMock()
    page.add_init_script = AsyncMock()
    page.expose_binding = AsyncMock()
    page.evaluate = AsyncMock(return_value={"ok": True})

    runner = CreatorWorkerRunner(page=page, bundle_code="X")
    result = asyncio.run(
        runner.execute(
            plan={"schema_version": 1, "steps": []},
            variables={"geo": "DE"},
            on_event=lambda *_: None,
        )
    )
    assert result["ok"] is True


def test_runner_propagates_failure():
    page = MagicMock()
    page.add_init_script = AsyncMock()
    page.expose_binding = AsyncMock()
    page.evaluate = AsyncMock(return_value={"ok": False, "error": "boom"})

    runner = CreatorWorkerRunner(page=page, bundle_code="X")
    result = asyncio.run(runner.execute(plan={"schema_version": 1, "steps": []}, variables={}, on_event=lambda *_: None))
    assert result["ok"] is False
    assert result["error"] == "boom"
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement** `apps/creator_worker/runner.py`:

```python
"""Запускает один PlanRun: инжектит бандл, гонит run_plan, реагирует на upload/checkpoint."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from core.creator_bridge.runner import CreatorRunner


class CreatorWorkerRunner:
    """Обёртка над CreatorRunner с обработкой request_upload и checkpoint."""

    def __init__(self, page: Any, bundle_code: str) -> None:
        self._page = page
        self._bundle = bundle_code

    async def execute(
        self,
        plan: dict[str, Any],
        variables: dict[str, Any],
        on_event: Callable[[str, Any], None],
    ) -> dict[str, Any]:
        runner = CreatorRunner(self._page, self._bundle)

        def _handler(event: str, payload: Any) -> None:
            on_event(event, payload)
            if event == "request_upload":
                # обработка добавляется в Task 5
                pass
            if event == "checkpoint_detected":
                on_event("checkpoint_detected", payload)

        await runner.attach(on_emit=_handler)
        result = await runner.run_plan(plan, variables)
        return result
```

- [ ] **Step 4: Run** → PASS.

- [ ] **Step 5: Commit** `feat(creator): CreatorWorkerRunner (one PlanRun executor)`.

---

### Task 5: Обработка `request_upload` в воркере

- [ ] **Step 1: Failing test** — добавить:

```python
def test_runner_handles_request_upload():
    page = MagicMock()
    page.add_init_script = AsyncMock()
    page.expose_binding = AsyncMock()
    page.evaluate = AsyncMock(return_value={"ok": True})
    page.locator = MagicMock()
    locator = MagicMock()
    locator.set_input_files = AsyncMock()
    page.locator.return_value = locator

    runner = CreatorWorkerRunner(page=page, bundle_code="X")
    events: list[tuple[str, dict]] = []

    async def fake_attach(on_emit):
        on_emit("request_upload", {"id": "u1", "paths": ["/tmp/a.jpg"], "selector": "input[data-fb-upload-id='u1']"})

    with patch.object(CreatorRunner, "attach", side_effect=fake_attach):
        asyncio.run(runner.execute(plan={"schema_version": 1, "steps": []}, variables={}, on_event=lambda *e: events.append(e)))

    page.locator.assert_called_once()
    locator.set_input_files.assert_awaited_once_with(["/tmp/a.jpg"])
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement** — обновить `_handler`:

```python
async def _on_upload(payload: dict[str, Any]) -> None:
    locator = self._page.locator(payload["selector"])
    await locator.set_input_files(payload["paths"])
    on_event("upload_done", {"id": payload["id"]})

def _handler(event: str, payload: Any) -> None:
    on_event(event, payload)
    if event == "request_upload":
        import asyncio as _a
        _a.create_task(_on_upload(payload))
```

- [ ] **Step 4: Run** → PASS.

- [ ] **Step 5: Commit** `feat(creator): worker handles request_upload via setInputFiles`.

---

### Task 6: `apps/creator_worker/main.py` — поллинг очереди

- [ ] **Step 1: Failing test** `tests/unit/test_creator_worker_main.py`:

```python
# Воркер должен поллить PlanRun.queued, поднимать профиль, исполнять, обновлять статус.
# Тестируем чистую функцию claim_next_run().
import asyncio
from datetime import UTC, datetime

import pytest

from apps.creator_worker.main import claim_next_run
from core.domain import PlanRunStatus
from core.models import Plan, PlanRun


@pytest.mark.asyncio
async def test_claim_next_run_returns_queued(in_memory_session):
    plan = Plan(name="t", schema_version=1, steps=[])
    in_memory_session.add(plan)
    await in_memory_session.flush()
    run = PlanRun(plan_id=plan.id, profile_id="p", variables={}, status=PlanRunStatus.QUEUED)
    in_memory_session.add(run)
    await in_memory_session.commit()
    claimed = await claim_next_run(in_memory_session)
    assert claimed is not None
    assert claimed.status == PlanRunStatus.RUNNING
```

(использовать существующую фикстуру `in_memory_session` если есть, иначе создать).

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement** `apps/creator_worker/main.py`:

```python
"""Воркер исполнения PlanRun. SELECT FOR UPDATE SKIP LOCKED, один профиль = одна очередь."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.creator_worker.runner import CreatorWorkerRunner
from core.browser.manager import VisionBrowserManager
from core.creator_bridge.bundle import load_bundle
from core.db import session_factory
from core.domain import PlanRunStatus
from core.models import Plan, PlanRun

log = logging.getLogger("creator_worker")


async def claim_next_run(session: AsyncSession) -> PlanRun | None:
    stmt = (
        select(PlanRun)
        .where(PlanRun.status == PlanRunStatus.QUEUED)
        .order_by(PlanRun.created_at)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    run = (await session.execute(stmt)).scalar_one_or_none()
    if run is None:
        return None
    run.status = PlanRunStatus.RUNNING
    run.started_at = datetime.now(UTC)
    await session.commit()
    return run


async def _execute(run: PlanRun) -> None:
    bundle = load_bundle()
    async with VisionBrowserManager(run.profile_id) as page:
        worker = CreatorWorkerRunner(page=page, bundle_code=bundle)

        async def _on_event(event: str, payload):
            async with session_factory() as s:
                cur = await s.get(PlanRun, run.id)
                if cur is None:
                    return
                cur.step_log = [*cur.step_log, {"event": event, "payload": payload}]
                await s.commit()
            if event == "checkpoint_detected":
                async with session_factory() as s:
                    cur = await s.get(PlanRun, run.id)
                    cur.status = PlanRunStatus.REQUIRES_ATTENTION
                    cur.finished_at = datetime.now(UTC)
                    await s.commit()

        async with session_factory() as s:
            plan = await s.get(Plan, run.plan_id)

        result = await worker.execute(
            plan={"schema_version": plan.schema_version, "steps": plan.steps},
            variables=run.variables,
            on_event=lambda e, p: asyncio.create_task(_on_event(e, p)),
        )

    async with session_factory() as s:
        cur = await s.get(PlanRun, run.id)
        cur.finished_at = datetime.now(UTC)
        if result["ok"]:
            cur.status = PlanRunStatus.SUCCESS
        elif cur.status != PlanRunStatus.REQUIRES_ATTENTION:
            cur.status = PlanRunStatus.FAILED
            cur.error_message = result.get("error", "unknown")
        await s.commit()


async def loop() -> None:
    while True:
        async with session_factory() as s:
            run = await claim_next_run(s)
        if run is None:
            await asyncio.sleep(2.0)
            continue
        try:
            await _execute(run)
        except Exception:
            log.exception("Сбой исполнения PlanRun %s", run.id)
            async with session_factory() as s:
                cur = await s.get(PlanRun, run.id)
                cur.status = PlanRunStatus.FAILED
                cur.error_message = "internal worker error"
                cur.finished_at = datetime.now(UTC)
                await s.commit()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(loop())


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run** → PASS.

- [ ] **Step 5: Commit** `feat(creator): creator_worker loop (claim/execute/update)`.

---

### Task 7: Root entrypoint `run_creator_worker.py`

- [ ] **Implement**:

```python
"""Корневой entrypoint для creator_worker (по образцу run_observer.py)."""

from apps.creator_worker.main import main

if __name__ == "__main__":
    main()
```

- [ ] **Commit** `feat(creator): run_creator_worker.py entrypoint`.

---

### Task 8: Реактивная остановка при checkpoint

- [ ] **Step 1: Test** — расширить `runner.py` чтобы подписываться на `page.on("framenavigated", ...)` и эмитить `checkpoint_detected` при попадании на `/checkpoint/`:

```python
def test_runner_emits_checkpoint_on_navigation(...):
    ...
```

- [ ] **Step 2: Implement** в `apps/creator_worker/runner.py`:

```python
def _subscribe_checkpoint(self, on_event):
    def _on_frame_nav(frame):
        if "/checkpoint/" in frame.url:
            on_event("checkpoint_detected", {"url": frame.url})
    self._page.on("framenavigated", _on_frame_nav)
```

- [ ] **Step 3: Commit** `feat(creator): reactive checkpoint detection in worker runner`.

---

### Task 9: Telegram alert при failed / requires_attention

- [ ] **Step 1: Test** что при переходе PlanRun в failed/requires_attention отправляется сообщение через `core.telegram.client`.
- [ ] **Step 2: Implement** в `_execute` после `cur.status = PlanRunStatus.FAILED|REQUIRES_ATTENTION` — вызвать `send_alert(...)` из `core/telegram/client.py`.
- [ ] **Step 3: Commit** `feat(creator): telegram alert on failed/requires_attention`.

---

### Task 10: End-of-phase smoke

- [ ] `pytest tests/unit/test_creator_*.py -v` → green.
- [ ] `cd services/browser-agent && npm test` → green.
- [ ] Manual: запустить `python -m apps.creator_recorder --profile=<id> --name=test` на staging — пройти простой шаг (set_age) — убедиться что записался `Plan` в БД.
- [ ] Manual: создать `PlanRun(plan_id=..., profile_id=..., variables={...})` → запустить `python run_creator_worker.py` → убедиться что шаг исполняется.

---

## Готово к Phase 5 когда

- `recorder.ts` пишет события и эмитит `recorded_step`/`recorded_unknown`.
- CLI `apps.creator_recorder` сохраняет Plan в БД.
- `creator_worker` поллит PlanRun, исполняет, обрабатывает upload/checkpoint, обновляет статус, шлёт Telegram-алерт.
