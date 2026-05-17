# Phase 2 — Core (humanizer / fiber / locator / registry / executor)

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) или `superpowers:executing-plans`.

**Goal:** Создать примитивы внутри браузер-бандла, на которых будут строиться все шаги: гуманизированный ввод, чтение React fiber, структурный locator, реестр шагов, базовый класс Step с встроенной идемпотентностью, executor плана.

**Architecture:** Всё in-browser (creator-бандл). Никаких CDP-вызовов из TS изнутри страницы — `humanizer` диспатчит реальные DOM-события (`pointerdown/up`, `keydown/keypress/keyup`, `input`) с гуманизированной задержкой. CDP-уровень `Input.dispatchMouseEvent` остаётся в Python-стороне как дополнительный канал (Phase 4 wiring).

**Tech Stack:** TypeScript 5.7, no runtime deps (всё что нужно есть в браузере).

---

## File Structure

- Create: `services/browser-agent/src/creator/humanizer.ts` — `humanClick`, `humanType`, `humanScroll`, `humanIdle`.
- Create: `services/browser-agent/src/creator/humanizer.test.ts`
- Create: `services/browser-agent/src/creator/fiber.ts` — `getFiber`, `getReactProps`, `walkUp`.
- Create: `services/browser-agent/src/creator/fiber.test.ts`
- Create: `services/browser-agent/src/creator/locator.ts` — `findByTestId`, `findByFiberRole`, `findByAriaLabel`, `findByNormalizedText`, `findBlock`.
- Create: `services/browser-agent/src/creator/locator.test.ts`
- Create: `services/browser-agent/src/creator/registry.ts` — `Map<string, Step>`, `registerStep`, `getStep`.
- Create: `services/browser-agent/src/creator/registry.test.ts`
- Create: `services/browser-agent/src/creator/steps/base.ts` — `BaseStep` с idempotency-логикой.
- Create: `services/browser-agent/src/creator/steps/base.test.ts`
- Create: `services/browser-agent/src/creator/executor.ts` — `runPlan`, эмит `step_started/skipped/finished/failed`.
- Create: `services/browser-agent/src/creator/executor.test.ts`
- Create: `services/browser-agent/src/creator/text.ts` — `normalizeText` (lowercase, trim, collapse whitespace, strip diacritics).
- Create: `services/browser-agent/src/creator/text.test.ts`
- Modify: `services/browser-agent/src/creator/index.ts` — подключить executor.

---

### Task 1: `text.ts` — нормализация текста для labelMap-матчинга

- [ ] **Step 1: Failing test** `creator/text.test.ts`:

```typescript
import { describe, it } from 'node:test';
import assert from 'node:assert';
import { normalizeText } from './text.js';

describe('normalizeText', () => {
  it('нижний регистр + триминг + схлопывает пробелы', () => {
    assert.equal(normalizeText('  Сайт   и звонки  '), 'сайт и звонки');
  });
  it('удаляет невидимые символы', () => {
    assert.equal(normalizeText('Web​site'), 'website');
  });
  it('идемпотентен', () => {
    const a = normalizeText('Сайт');
    assert.equal(normalizeText(a), a);
  });
});
```

- [ ] **Step 2: Run** `npm test` → FAIL.

- [ ] **Step 3: Implement**

```typescript
export function normalizeText(input: string): string {
  return input
    .replace(/[​-‏ - ﻿]/g, '')
    .toLowerCase()
    .trim()
    .replace(/\s+/g, ' ');
}
```

- [ ] **Step 4: Run** → PASS.

- [ ] **Step 5: Commit**

```bash
git add services/browser-agent/src/creator/text*.ts
git commit -m "feat(creator): normalizeText helper"
```

---

### Task 2: `humanizer.ts` — humanIdle (паузы)

- [ ] **Step 1: Failing test** `creator/humanizer.test.ts`:

```typescript
import { describe, it } from 'node:test';
import assert from 'node:assert';
import { humanIdle, IdleRange } from './humanizer.js';

describe('humanIdle', () => {
  it('ждёт в пределах диапазона', async () => {
    const start = Date.now();
    await humanIdle(IdleRange.SHORT);
    const elapsed = Date.now() - start;
    assert.ok(elapsed >= 50 && elapsed <= 600, `elapsed=${elapsed}`);
  });
});
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement** `creator/humanizer.ts`:

```typescript
export const IdleRange = {
  SHORT: [80, 250] as const,
  BETWEEN_STEPS: [600, 2500] as const,
  BETWEEN_SCENES: [3000, 8000] as const,
  TYPING: [40, 180] as const,
  TYPING_BURST_PAUSE: [200, 800] as const,
} as const;

export type IdleRangeKey = readonly [number, number];

function rand(min: number, max: number): number {
  return min + Math.random() * (max - min);
}

export function humanIdle(range: IdleRangeKey): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, rand(range[0], range[1])));
}
```

- [ ] **Step 4: Run** → PASS.

- [ ] **Step 5: Commit**

```bash
git add services/browser-agent/src/creator/humanizer*.ts
git commit -m "feat(creator): humanIdle + IdleRange constants"
```

---

### Task 3: `humanizer.humanClick`

- [ ] **Step 1: Failing test** — добавить в `humanizer.test.ts`:

```typescript
import { humanClick } from './humanizer.js';

describe('humanClick', () => {
  it('диспатчит pointerdown→pointerup→click на элемент', async () => {
    const div = makeDivInJsdom();  // helper, см. ниже
    const events: string[] = [];
    ['pointerover', 'pointermove', 'pointerdown', 'pointerup', 'click'].forEach((t) =>
      div.addEventListener(t, () => events.push(t)),
    );
    await humanClick(div);
    assert.deepEqual(events.slice(-3), ['pointerdown', 'pointerup', 'click']);
  });
});
```

`makeDivInJsdom` создаёт минимальный DOM через `globalThis.document = new (await import('jsdom')).JSDOM().window.document` — добавить как dev-dep `jsdom` в `services/browser-agent/package.json`:

```bash
cd services/browser-agent && npm install --save-dev jsdom @types/jsdom
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement** — добавить в `humanizer.ts`:

```typescript
function dispatchPointer(el: Element, type: string, x: number, y: number): void {
  const ev = new PointerEvent(type, {
    bubbles: true,
    cancelable: true,
    composed: true,
    clientX: x,
    clientY: y,
    pointerType: 'mouse',
    isPrimary: true,
  });
  el.dispatchEvent(ev);
}

async function bezierHover(el: Element): Promise<void> {
  const rect = el.getBoundingClientRect();
  const tx = rect.left + rect.width / 2;
  const ty = rect.top + rect.height / 2;
  dispatchPointer(el, 'pointerover', tx, ty);
  const steps = 6 + Math.floor(Math.random() * 6);
  for (let i = 1; i <= steps; i++) {
    dispatchPointer(el, 'pointermove', tx + Math.random() * 2 - 1, ty + Math.random() * 2 - 1);
    await humanIdle([8, 24] as const);
  }
}

export async function humanClick(el: Element): Promise<void> {
  await bezierHover(el);
  await humanIdle(IdleRange.SHORT);
  const rect = el.getBoundingClientRect();
  const x = rect.left + rect.width / 2;
  const y = rect.top + rect.height / 2;
  dispatchPointer(el, 'pointerdown', x, y);
  await humanIdle([20, 90] as const);
  dispatchPointer(el, 'pointerup', x, y);
  el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, clientX: x, clientY: y }));
}
```

- [ ] **Step 4: Run** → PASS.

- [ ] **Step 5: Commit**

```bash
git add services/browser-agent/src/creator/humanizer.ts services/browser-agent/src/creator/humanizer.test.ts services/browser-agent/package.json services/browser-agent/package-lock.json
git commit -m "feat(creator): humanClick (pointer events + bezier hover)"
```

---

### Task 4: `humanizer.humanType`

- [ ] **Step 1: Failing test** — добавить:

```typescript
import { humanType } from './humanizer.js';

describe('humanType', () => {
  it('вводит текст символ за символом и диспатчит input/keydown', async () => {
    const input = document.createElement('input');
    document.body.appendChild(input);
    const events: string[] = [];
    ['keydown', 'keypress', 'input', 'keyup'].forEach((t) =>
      input.addEventListener(t, () => events.push(t)),
    );
    await humanType(input, 'ab');
    assert.equal(input.value, 'ab');
    assert.ok(events.includes('input'));
    assert.ok(events.includes('keydown'));
  });
});
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement** — в `humanizer.ts`:

```typescript
function setNativeInputValue(el: HTMLInputElement | HTMLTextAreaElement, value: string): void {
  const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
  setter?.call(el, value);
}

export async function humanType(el: HTMLInputElement | HTMLTextAreaElement, text: string): Promise<void> {
  el.focus();
  await humanIdle(IdleRange.SHORT);
  let current = '';
  for (let i = 0; i < text.length; i++) {
    const ch = text[i]!;
    el.dispatchEvent(new KeyboardEvent('keydown', { key: ch, bubbles: true }));
    el.dispatchEvent(new KeyboardEvent('keypress', { key: ch, bubbles: true }));
    current += ch;
    setNativeInputValue(el, current);
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new KeyboardEvent('keyup', { key: ch, bubbles: true }));
    await humanIdle(IdleRange.TYPING);
    if (i > 0 && i % (3 + Math.floor(Math.random() * 6)) === 0) {
      await humanIdle(IdleRange.TYPING_BURST_PAUSE);
    }
  }
  el.dispatchEvent(new Event('change', { bubbles: true }));
  el.blur();
}
```

- [ ] **Step 4: Run** → PASS.

- [ ] **Step 5: Commit**

```bash
git add services/browser-agent/src/creator/humanizer.ts services/browser-agent/src/creator/humanizer.test.ts
git commit -m "feat(creator): humanType (native value setter + key events)"
```

---

### Task 5: `humanizer.humanScroll`

- [ ] **Step 1: Failing test**:

```typescript
import { humanScroll } from './humanizer.js';

describe('humanScroll', () => {
  it('диспатчит wheel-события', async () => {
    const div = document.createElement('div');
    document.body.appendChild(div);
    let count = 0;
    div.addEventListener('wheel', () => count++);
    await humanScroll(div, 300);
    assert.ok(count >= 3, `wheel events=${count}`);
  });
});
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement**:

```typescript
export async function humanScroll(el: Element, deltaY: number): Promise<void> {
  const ticks = 6 + Math.floor(Math.random() * 6);
  const per = deltaY / ticks;
  for (let i = 0; i < ticks; i++) {
    el.dispatchEvent(new WheelEvent('wheel', { bubbles: true, deltaY: per * (0.7 + Math.random() * 0.6) }));
    await humanIdle([30, 110] as const);
  }
}
```

- [ ] **Step 4: Run** → PASS.

- [ ] **Step 5: Commit**

```bash
git add services/browser-agent/src/creator/humanizer.ts services/browser-agent/src/creator/humanizer.test.ts
git commit -m "feat(creator): humanScroll (wheel events with variable velocity)"
```

---

### Task 6: `fiber.ts` — чтение React internals

- [ ] **Step 1: Failing test** `creator/fiber.test.ts`:

```typescript
import { describe, it } from 'node:test';
import assert from 'node:assert';
import { getFiber, getReactProps } from './fiber.js';

describe('fiber', () => {
  it('возвращает null если у элемента нет fiber-ключа', () => {
    const div = document.createElement('div');
    assert.equal(getFiber(div), null);
    assert.equal(getReactProps(div), null);
  });

  it('читает __reactProps$* по динамическому ключу', () => {
    const div: any = document.createElement('div');
    div.__reactProps$abc = { foo: 'bar' };
    assert.deepEqual(getReactProps(div), { foo: 'bar' });
  });
});
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement** `creator/fiber.ts`:

```typescript
function findKey(el: Element, prefix: string): string | null {
  for (const key of Object.keys(el)) {
    if (key.startsWith(prefix)) return key;
  }
  return null;
}

export function getFiber(el: Element): unknown {
  const key = findKey(el, '__reactFiber$');
  return key ? (el as any)[key] : null;
}

export function getReactProps(el: Element): Record<string, unknown> | null {
  const key = findKey(el, '__reactProps$');
  return key ? ((el as any)[key] as Record<string, unknown>) : null;
}

export function walkUp(el: Element, predicate: (n: Element) => boolean, maxDepth = 12): Element | null {
  let cur: Element | null = el;
  let depth = 0;
  while (cur && depth < maxDepth) {
    if (predicate(cur)) return cur;
    cur = cur.parentElement;
    depth++;
  }
  return null;
}
```

- [ ] **Step 4: Run** → PASS.

- [ ] **Step 5: Commit**

```bash
git add services/browser-agent/src/creator/fiber*.ts
git commit -m "feat(creator): fiber helpers (getFiber/getReactProps/walkUp)"
```

---

### Task 7: `locator.ts` — structural lookup

- [ ] **Step 1: Failing test** `creator/locator.test.ts`:

```typescript
import { describe, it } from 'node:test';
import assert from 'node:assert';
import { findByTestId, findByAriaLabel, findByNormalizedText, findBlock } from './locator.js';

describe('locator', () => {
  it('findByTestId', () => {
    const el = document.createElement('div');
    el.setAttribute('data-testid', 'geo');
    document.body.appendChild(el);
    assert.strictEqual(findByTestId('geo'), el);
  });

  it('findByAriaLabel', () => {
    const el = document.createElement('button');
    el.setAttribute('aria-label', 'Сохранить черновик');
    document.body.appendChild(el);
    assert.strictEqual(findByAriaLabel(['Сохранить черновик', 'Save draft']), el);
  });

  it('findByNormalizedText матчит нормализованный label', () => {
    const el = document.createElement('label');
    el.textContent = '  Сайт   и звонки  ';
    document.body.appendChild(el);
    assert.strictEqual(findByNormalizedText(['сайт и звонки']), el);
  });

  it('findBlock пробует testid → aria → text fallback в указанном порядке', () => {
    const el = document.createElement('section');
    el.setAttribute('data-testid', 'budget');
    document.body.appendChild(el);
    const found = findBlock({ testid: 'budget', aria: ['Бюджет'], text: ['Бюджет'] });
    assert.strictEqual(found, el);
  });
});
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement** `creator/locator.ts`:

```typescript
import { normalizeText } from './text.js';
import { getReactProps } from './fiber.js';

export interface BlockLookup {
  testid?: string;
  fiberRole?: string;
  aria?: string[];
  text?: string[];
}

export function findByTestId(testid: string, root: ParentNode = document): Element | null {
  return root.querySelector(`[data-testid="${CSS.escape(testid)}"]`);
}

export function findByAriaLabel(labels: string[], root: ParentNode = document): Element | null {
  const targets = new Set(labels.map(normalizeText));
  for (const el of Array.from(root.querySelectorAll('[aria-label]'))) {
    const aria = normalizeText(el.getAttribute('aria-label') || '');
    if (targets.has(aria)) return el;
  }
  return null;
}

export function findByFiberRole(role: string, root: ParentNode = document): Element | null {
  for (const el of Array.from(root.querySelectorAll<HTMLElement>('*'))) {
    const props = getReactProps(el);
    if (props && (props as any).role === role) return el;
  }
  return null;
}

export function findByNormalizedText(texts: string[], root: ParentNode = document): Element | null {
  const targets = new Set(texts.map(normalizeText));
  const walker = document.createTreeWalker(root as Node, NodeFilter.SHOW_ELEMENT);
  let cur = walker.currentNode as Element | null;
  while (cur) {
    const direct = Array.from(cur.childNodes)
      .filter((n) => n.nodeType === Node.TEXT_NODE)
      .map((n) => normalizeText(n.textContent || ''))
      .join(' ')
      .trim();
    if (direct && targets.has(direct)) return cur;
    cur = walker.nextNode() as Element | null;
  }
  return null;
}

export function findBlock(spec: BlockLookup, root: ParentNode = document): Element | null {
  if (spec.testid) {
    const el = findByTestId(spec.testid, root);
    if (el) return el;
  }
  if (spec.fiberRole) {
    const el = findByFiberRole(spec.fiberRole, root);
    if (el) return el;
  }
  if (spec.aria?.length) {
    const el = findByAriaLabel(spec.aria, root);
    if (el) return el;
  }
  if (spec.text?.length) {
    const el = findByNormalizedText(spec.text, root);
    if (el) return el;
  }
  return null;
}
```

- [ ] **Step 4: Run** → PASS.

- [ ] **Step 5: Commit**

```bash
git add services/browser-agent/src/creator/locator*.ts
git commit -m "feat(creator): structural locator (testid → fiber-role → aria → text)"
```

---

### Task 8: `registry.ts`

- [ ] **Step 1: Failing test** `creator/registry.test.ts`:

```typescript
import { describe, it, beforeEach } from 'node:test';
import assert from 'node:assert';
import { registerStep, getStep, listSteps, clearRegistry } from './registry.js';
import type { Step } from './types.js';

const dummy: Step = {
  name: 'dummy',
  detect: () => ({ kind: 'unknown' }),
  isSatisfied: () => false,
  execute: async () => ({}),
};

describe('registry', () => {
  beforeEach(() => clearRegistry());

  it('регистрирует и возвращает шаг по имени', () => {
    registerStep(dummy);
    assert.strictEqual(getStep('dummy'), dummy);
  });

  it('listSteps возвращает все', () => {
    registerStep(dummy);
    assert.deepEqual(listSteps().map((s) => s.name), ['dummy']);
  });

  it('падает при попытке зарегистрировать дубликат', () => {
    registerStep(dummy);
    assert.throws(() => registerStep(dummy), /already registered/);
  });
});
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement** `creator/registry.ts`:

```typescript
import type { Step } from './types.js';

const _registry = new Map<string, Step>();

export function registerStep(step: Step): void {
  if (_registry.has(step.name)) {
    throw new Error(`Step ${step.name} already registered`);
  }
  _registry.set(step.name, step);
}

export function getStep(name: string): Step | undefined {
  return _registry.get(name);
}

export function listSteps(): Step[] {
  return Array.from(_registry.values());
}

export function clearRegistry(): void {
  _registry.clear();
}
```

- [ ] **Step 4: Run** → PASS.

- [ ] **Step 5: Commit**

```bash
git add services/browser-agent/src/creator/registry*.ts
git commit -m "feat(creator): step registry"
```

---

### Task 9: `steps/base.ts` — `BaseStep` с встроенной idempotency

- [ ] **Step 1: Failing test** `creator/steps/base.test.ts`:

```typescript
import { describe, it } from 'node:test';
import assert from 'node:assert';
import { BaseStep } from './base.js';
import type { PlanContext, StepState } from '../types.js';

class FakeStep extends BaseStep<{ value: string }, void> {
  name = 'fake';
  executed = false;
  detect(): StepState {
    return { kind: 'matched', current: 'A' };
  }
  isSatisfied(state: StepState, input: { value: string }): boolean {
    return state.current === input.value;
  }
  protected async run(): Promise<void> {
    this.executed = true;
  }
}

const ctx: PlanContext = { variables: {}, emit: () => {} };

describe('BaseStep', () => {
  it('skip когда уже satisfied', async () => {
    const s = new FakeStep();
    const state = s.detect(ctx);
    await s.execute(state as any, { value: 'A' }, ctx);
    assert.equal(s.executed, false);
  });

  it('исполняет когда не satisfied', async () => {
    const s = new FakeStep();
    const state = s.detect(ctx);
    await s.execute(state as any, { value: 'B' }, ctx);
    assert.equal(s.executed, true);
  });
});
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement** `creator/steps/base.ts`:

```typescript
import type { PlanContext, Step, StepState } from '../types.js';

export abstract class BaseStep<I = unknown, O = unknown> implements Step<I, O> {
  abstract name: string;
  abstract detect(ctx: PlanContext): Promise<StepState> | StepState;
  abstract isSatisfied(state: StepState, input: I): boolean;

  protected abstract run(state: StepState, input: I, ctx: PlanContext): Promise<O>;

  async execute(state: StepState, input: I, ctx: PlanContext): Promise<O> {
    if (this.isSatisfied(state, input)) {
      ctx.emit('step_skipped', { step: this.name, reason: 'already_satisfied' });
      return undefined as unknown as O;
    }
    return await this.run(state, input, ctx);
  }
}
```

- [ ] **Step 4: Run** → PASS.

- [ ] **Step 5: Commit**

```bash
git add services/browser-agent/src/creator/steps/base*.ts
git commit -m "feat(creator): BaseStep with built-in idempotency"
```

---

### Task 10: `executor.ts` — `runPlan` с подстановкой переменных

- [ ] **Step 1: Failing test** `creator/executor.test.ts`:

```typescript
import { describe, it, beforeEach } from 'node:test';
import assert from 'node:assert';
import { runPlan, interpolate } from './executor.js';
import { registerStep, clearRegistry } from './registry.js';
import { BaseStep } from './steps/base.js';
import type { StepState } from './types.js';

class Capture extends BaseStep<{ v: string }, void> {
  name = 'cap';
  received: string | null = null;
  detect(): StepState { return { kind: 'unknown' }; }
  isSatisfied(): boolean { return false; }
  protected async run(_s: StepState, i: { v: string }): Promise<void> {
    this.received = i.v;
  }
}

describe('executor', () => {
  beforeEach(() => clearRegistry());

  it('interpolate подставляет {{geo}}', () => {
    const out = interpolate({ v: '{{geo}}-{{offer.code}}' }, { geo: 'DE', offer: { code: 'CR2' } });
    assert.deepEqual(out, { v: 'DE-CR2' });
  });

  it('runPlan выполняет шаги по очереди и эмитит events', async () => {
    const step = new Capture();
    registerStep(step);
    const events: Array<[string, unknown]> = [];
    const result = await runPlan(
      { schema_version: 1, steps: [{ step: 'cap', input: { v: '{{geo}}' } }] },
      { geo: 'DE' },
      (e, p) => events.push([e, p]),
    );
    assert.equal(result.ok, true);
    assert.equal(step.received, 'DE');
    const types = events.map(([e]) => e);
    assert.ok(types.includes('step_started'));
    assert.ok(types.includes('step_finished'));
  });

  it('runPlan возвращает {ok:false} при неизвестном шаге', async () => {
    const result = await runPlan(
      { schema_version: 1, steps: [{ step: 'nope', input: {} }] },
      {},
      () => {},
    );
    assert.equal(result.ok, false);
    assert.match(result.error || '', /unknown step/i);
  });
});
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement** `creator/executor.ts`:

```typescript
import { getStep } from './registry.js';
import { humanIdle, IdleRange } from './humanizer.js';
import type { Plan, PlanContext } from './types.js';

export type Emit = (event: string, payload?: unknown) => void;

const TEMPLATE_RE = /\{\{\s*([\w.]+)\s*\}\}/g;

function resolvePath(obj: Record<string, unknown>, path: string): unknown {
  return path.split('.').reduce<unknown>((acc, key) => {
    if (acc && typeof acc === 'object' && key in (acc as Record<string, unknown>)) {
      return (acc as Record<string, unknown>)[key];
    }
    return undefined;
  }, obj);
}

export function interpolate<T>(input: T, vars: Record<string, unknown>): T {
  if (typeof input === 'string') {
    return input.replace(TEMPLATE_RE, (_, p) => {
      const v = resolvePath(vars, p);
      return v == null ? '' : String(v);
    }) as unknown as T;
  }
  if (Array.isArray(input)) return input.map((x) => interpolate(x, vars)) as unknown as T;
  if (input && typeof input === 'object') {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(input as Record<string, unknown>)) {
      out[k] = interpolate(v, vars);
    }
    return out as unknown as T;
  }
  return input;
}

export async function runPlan(
  plan: Plan,
  variables: Record<string, unknown>,
  emit: Emit,
): Promise<{ ok: boolean; error?: string }> {
  const ctx: PlanContext = { variables, emit };
  for (const step of plan.steps) {
    const impl = getStep(step.step);
    if (!impl) {
      emit('step_failed', { step: step.step, error: 'unknown step' });
      return { ok: false, error: `unknown step: ${step.step}` };
    }
    const input = interpolate(step.input, variables);
    emit('step_started', { step: step.step });
    try {
      const state = await impl.detect(ctx);
      await impl.execute(state, input, ctx);
      emit('step_finished', { step: step.step });
    } catch (e: any) {
      emit('step_failed', { step: step.step, error: String(e?.message ?? e) });
      return { ok: false, error: String(e?.message ?? e) };
    }
    await humanIdle(IdleRange.BETWEEN_STEPS);
  }
  return { ok: true };
}
```

- [ ] **Step 4: Run** → PASS.

- [ ] **Step 5: Commit**

```bash
git add services/browser-agent/src/creator/executor*.ts
git commit -m "feat(creator): runPlan executor with variable interpolation"
```

---

### Task 11: Подключить executor в `creator/index.ts`

- [ ] **Step 1: Failing test** — обновить `creator/index.test.ts`:

```typescript
it('window.__fbAgent.run делегирует в runPlan', async () => {
  const win: any = (globalThis as any).window;
  const result = await win.__fbAgent.run({ schema_version: 1, steps: [] }, {});
  assert.equal(result.ok, true);
});
```

- [ ] **Step 2: Run** → FAIL (run возвращает `ok:false`).

- [ ] **Step 3: Implement** — обновить `creator/index.ts`:

```typescript
import { runPlan } from './executor.js';
import type { Plan } from './types.js';

const api = {
  version: '2.0.0',
  async run(plan: Plan, variables: Record<string, unknown>) {
    const emit = (event: string, payload?: unknown) => {
      const fn = (globalThis as any).fbAgentEmit;
      if (typeof fn === 'function') fn(event, payload);
    };
    return runPlan(plan, variables, emit);
  },
  async startRecording(_planName: string): Promise<void> {
    throw new Error('recorder wired in phase 4');
  },
  async stopRecording(): Promise<void> {
    throw new Error('recorder wired in phase 4');
  },
};

(globalThis as any).window = (globalThis as any).window ?? {};
(globalThis as any).window.__fbAgent = api;

export { api };
```

- [ ] **Step 4: Run** → PASS.

- [ ] **Step 5: Commit**

```bash
git add services/browser-agent/src/creator/index.ts services/browser-agent/src/creator/index.test.ts
git commit -m "feat(creator): wire runPlan into window.__fbAgent.run"
```

---

### Task 12: Lint-rule — запрет `el.click()` / `el.value=` в `creator/steps/`

- [ ] **Step 1:** Добавить `services/browser-agent/.eslintrc.creator.js` или `.eslintrc.json` с правилом:

```json
{
  "overrides": [
    {
      "files": ["src/creator/steps/**/*.ts"],
      "rules": {
        "no-restricted-syntax": [
          "error",
          { "selector": "CallExpression[callee.property.name='click']", "message": "Используй humanClick() из humanizer.ts" },
          { "selector": "AssignmentExpression[left.property.name='value']", "message": "Используй humanType() из humanizer.ts" }
        ]
      }
    }
  ]
}
```

- [ ] **Step 2:** `npm run lint` → clean.

- [ ] **Step 3: Commit**

```bash
git add services/browser-agent/.eslintrc*
git commit -m "chore(creator): lint rule forbidding el.click()/el.value= in steps/"
```

---

### Task 13: End-of-phase smoke

- [ ] **Step 1:** `cd services/browser-agent && npm run build && npm test` → all green.
- [ ] **Step 2:** Проверить что `dist/creator/index.js` существует и содержит `runPlan`/`window.__fbAgent`.

---

## Готово к Phase 3 когда

- `humanClick`/`humanType`/`humanScroll`/`humanIdle` работают и протестированы в jsdom.
- `fiber.ts`, `locator.ts`, `text.ts` готовы.
- `registry.ts` принимает шаги, `BaseStep` обеспечивает idempotency, `executor.ts` гоняет план с подстановкой переменных и эмитит events.
- Lint запрещает прямые `.click()`/`.value=` в `steps/`.
