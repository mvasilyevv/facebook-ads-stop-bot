# Phase 3 — Enums + ~25 Steps

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) или `superpowers:executing-plans`.

**Goal:** Реализовать все канонические enum'ы и все ~25 шагов с паритетом к старому `core/campaign_creator/steps/`. Каждый шаг = отдельный TS-файл, использует `BaseStep`, `humanizer`, `locator`, `enums`, регистрируется в `creator/steps/index.ts`.

**Architecture:** Шаг получает на вход типизированный input (enum / number / string / array). `detect()` структурно находит блок и читает текущее значение через fiber или selected-option. `isSatisfied()` сравнивает state.current с input. `execute()` открывает дропдаун/вводит текст/загружает файл через humanizer.

**Tech Stack:** TypeScript 5.7 (no runtime deps).

---

## File Structure

### Enums (`services/browser-agent/src/creator/enums/`)

- `conversion-location.ts` — `ConversionLocation` enum + `conversionLocationLabels: LabelMap`
- `pixel-event.ts` — `PixelEvent` (Purchase, Lead, CompleteRegistration, …)
- `optimization-goal.ts` — `OptimizationGoal`
- `attribution.ts` — `AttributionWindow`
- `cta.ts` — `CallToAction`
- `objective.ts` — `Objective`
- `currency.ts` — `Currency`
- `placement.ts` — `Placement`
- `index.ts` — реэкспорт всех + общий тип `LabelMap<T>`

### Steps (`services/browser-agent/src/creator/steps/`)

| Файл | Аналог в старом коде | Input shape |
|------|----------------------|-------------|
| `set_geo.ts` | set_geo.py | `{ countries: string[] }` |
| `set_age.ts` | set_age.py | `{ min: number; max: number }` |
| `set_conversion_location.ts` | set_conversion_location.py | `{ value: ConversionLocation }` |
| `set_pixel_event.ts` | set_pixel_event.py | `{ pixelId: string; event: PixelEvent }` |
| `set_optimization_goal.ts` | (был в plan) | `{ value: OptimizationGoal }` |
| `set_attribution.ts` | set_attribution.py | `{ value: AttributionWindow }` |
| `set_budget.ts` | set_budget.py | `{ amount: number; currency: Currency }` |
| `set_schedule_start.ts` | set_schedule_start.py | `{ isoDate: string }` |
| `set_cta.ts` | set_cta.py | `{ value: CallToAction }` |
| `set_tracking_url.ts` | set_tracking_url.py | `{ url: string }` |
| `fill_texts.ts` | fill_texts.py | `{ primary: string; headline: string; description?: string }` |
| `upload_creatives.ts` | upload_creatives.py | `{ paths: string[] }` |
| `create_campaign.ts` | create_campaign.py | `{ name: string; objective: Objective }` |
| `create_adset.ts` | create_adset.py | `{ name: string }` |
| `duplicate_adset.ts` | duplicate_adset.py | `{ sourceName: string; newName: string }` |
| `duplicate_ad.ts` | duplicate_ad.py | `{ sourceName: string; newName: string }` |
| `rename_adset.ts` | rename_adset.py | `{ from: string; to: string }` |
| `rename_ad.ts` | rename_ad.py | `{ from: string; to: string }` |
| `reattach_creative.ts` | reattach_creative.py | `{ adName: string; paths: string[] }` |
| `switch_to_adset.ts` | switch_to_adset.py | `{ name: string }` |
| `click_next.ts` | click_next.py | `{}` |
| `save_draft.ts` | save_draft.py | `{}` |
| `unknown.ts` | — | `{ raw: unknown }` (всегда падает с описанием) |
| `index.ts` | — | imports + `registerStep(...)` для всех |

---

## Универсальный паттерн шага с enum-выбором (template)

Все шаги типа «выбор из дропдауна по labelMap» следуют одной структуре. Перед реализацией конкретных шагов нужно создать helper `selectFromDropdown`, чтобы избежать дублирования.

### Task 0: Helper `select-from-dropdown.ts`

- [ ] **Step 1: Failing test** `creator/steps/_helpers/select-from-dropdown.test.ts`:

```typescript
import { describe, it } from 'node:test';
import assert from 'node:assert';
import { resolveLabelToEnum } from './select-from-dropdown.js';

const labels = {
  WEBSITE: { ru: ['Сайт', 'Веб-сайт'], en: ['Website'] },
  APP: { ru: ['Приложение'], en: ['App'] },
} as const;

describe('resolveLabelToEnum', () => {
  it('матчит ru синоним', () => assert.equal(resolveLabelToEnum('  сайт ', labels), 'WEBSITE'));
  it('матчит en label', () => assert.equal(resolveLabelToEnum('App', labels), 'APP'));
  it('возвращает null при отсутствии', () => assert.equal(resolveLabelToEnum('xxx', labels), null));
});
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement** `creator/steps/_helpers/select-from-dropdown.ts`:

```typescript
import { humanClick, humanIdle, IdleRange } from '../../humanizer.js';
import { findBlock, findByNormalizedText } from '../../locator.js';
import { normalizeText } from '../../text.js';
import type { BlockLookup } from '../../locator.js';

export type LabelMap<T extends string> = Record<T, { ru: string[]; en: string[] }>;

export function resolveLabelToEnum<T extends string>(label: string, labels: LabelMap<T>): T | null {
  const norm = normalizeText(label);
  for (const [enumKey, syns] of Object.entries(labels) as [T, { ru: string[]; en: string[] }][]) {
    const all = [...syns.ru, ...syns.en].map(normalizeText);
    if (all.includes(norm)) return enumKey;
  }
  return null;
}

export interface DropdownSpec<T extends string> {
  block: BlockLookup;
  labels: LabelMap<T>;
}

export async function readSelectedValue<T extends string>(
  spec: DropdownSpec<T>,
): Promise<T | null> {
  const block = findBlock(spec.block);
  if (!block) return null;
  const visible = block.querySelector('[aria-selected="true"], [data-selected="true"], button[aria-haspopup="listbox"]');
  const text = (visible?.textContent ?? '').trim();
  if (!text) return null;
  return resolveLabelToEnum(text, spec.labels);
}

export async function selectValue<T extends string>(
  spec: DropdownSpec<T>,
  target: T,
): Promise<void> {
  const block = findBlock(spec.block);
  if (!block) throw new Error(`Блок не найден: ${JSON.stringify(spec.block)}`);
  const trigger = block.querySelector<HTMLElement>('button[aria-haspopup="listbox"], [role="combobox"]');
  if (!trigger) throw new Error('Trigger дропдауна не найден');
  await humanClick(trigger);
  await humanIdle(IdleRange.SHORT);
  const syns = spec.labels[target];
  if (!syns) throw new Error(`Unknown enum value: ${target}`);
  const option = findByNormalizedText([...syns.ru, ...syns.en]);
  if (!option) {
    throw new Error(`Опция "${target}" не найдена в дропдауне (синонимы: ${[...syns.ru, ...syns.en].join(', ')})`);
  }
  await humanClick(option);
  await humanIdle(IdleRange.BETWEEN_STEPS);
}
```

- [ ] **Step 4: Run** → PASS.

- [ ] **Step 5: Commit**

```bash
git add services/browser-agent/src/creator/steps/_helpers/
git commit -m "feat(creator): helper для выбора из дропдауна по LabelMap"
```

---

## Enums — общий шаблон (повторить для каждого enum)

### Task 1: `enums/index.ts` + `LabelMap` type

- [ ] **Step 1: Implement** `creator/enums/index.ts`:

```typescript
export type LabelMap<T extends string> = Record<T, { ru: string[]; en: string[] }>;

export * from './conversion-location.js';
export * from './pixel-event.js';
export * from './optimization-goal.js';
export * from './attribution.js';
export * from './cta.js';
export * from './objective.js';
export * from './currency.js';
export * from './placement.js';
```

- [ ] **Step 2: Commit** после того как все enum-файлы созданы (см. ниже).

---

### Task 2: `enums/conversion-location.ts`

- [ ] **Step 1: Test** `enums/conversion-location.test.ts`:

```typescript
import { describe, it } from 'node:test';
import assert from 'node:assert';
import { ConversionLocation, conversionLocationLabels } from './conversion-location.js';

describe('ConversionLocation', () => {
  it('перечисляет все ожидаемые значения', () => {
    assert.deepEqual(Object.values(ConversionLocation).sort(), ['APP', 'MESSENGER', 'WEBSITE', 'WEBSITE_AND_CALLS']);
  });
  it('у каждого enum есть ru и en синонимы', () => {
    for (const k of Object.values(ConversionLocation)) {
      const labels = (conversionLocationLabels as any)[k];
      assert.ok(labels.ru.length > 0 && labels.en.length > 0, `нет синонимов для ${k}`);
    }
  });
});
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement** `enums/conversion-location.ts`:

```typescript
import type { LabelMap } from './index.js';

export const ConversionLocation = {
  WEBSITE: 'WEBSITE',
  WEBSITE_AND_CALLS: 'WEBSITE_AND_CALLS',
  APP: 'APP',
  MESSENGER: 'MESSENGER',
} as const;
export type ConversionLocation = (typeof ConversionLocation)[keyof typeof ConversionLocation];

export const conversionLocationLabels: LabelMap<ConversionLocation> = {
  WEBSITE: { ru: ['Сайт', 'Веб-сайт'], en: ['Website', 'Web site'] },
  WEBSITE_AND_CALLS: { ru: ['Сайт и звонки'], en: ['Website and calls'] },
  APP: { ru: ['Приложение'], en: ['App'] },
  MESSENGER: { ru: ['Messenger'], en: ['Messenger'] },
};
```

- [ ] **Step 4: Run** → PASS.

- [ ] **Step 5: Commit** (после прохождения всех enum-тестов; см. Task 9).

---

### Task 3: `enums/pixel-event.ts`

- [ ] **Step 1: Test** (по образцу Task 2).

- [ ] **Step 2: Implement**:

```typescript
import type { LabelMap } from './index.js';

export const PixelEvent = {
  PURCHASE: 'PURCHASE',
  LEAD: 'LEAD',
  COMPLETE_REGISTRATION: 'COMPLETE_REGISTRATION',
  ADD_TO_CART: 'ADD_TO_CART',
  INITIATE_CHECKOUT: 'INITIATE_CHECKOUT',
  SUBSCRIBE: 'SUBSCRIBE',
  ADD_PAYMENT_INFO: 'ADD_PAYMENT_INFO',
  CONTACT: 'CONTACT',
  SEARCH: 'SEARCH',
  VIEW_CONTENT: 'VIEW_CONTENT',
} as const;
export type PixelEvent = (typeof PixelEvent)[keyof typeof PixelEvent];

export const pixelEventLabels: LabelMap<PixelEvent> = {
  PURCHASE: { ru: ['Покупка'], en: ['Purchase'] },
  LEAD: { ru: ['Лид'], en: ['Lead'] },
  COMPLETE_REGISTRATION: { ru: ['Завершённая регистрация', 'Регистрация'], en: ['Complete registration', 'Completed registration'] },
  ADD_TO_CART: { ru: ['Добавление в корзину'], en: ['Add to cart'] },
  INITIATE_CHECKOUT: { ru: ['Начало оформления'], en: ['Initiate checkout'] },
  SUBSCRIBE: { ru: ['Подписка'], en: ['Subscribe'] },
  ADD_PAYMENT_INFO: { ru: ['Добавление платёжной информации'], en: ['Add payment info'] },
  CONTACT: { ru: ['Контакт'], en: ['Contact'] },
  SEARCH: { ru: ['Поиск'], en: ['Search'] },
  VIEW_CONTENT: { ru: ['Просмотр контента'], en: ['View content'] },
};
```

---

### Task 4: `enums/optimization-goal.ts`

- [ ] **Test + Implement** аналогично:

```typescript
import type { LabelMap } from './index.js';

export const OptimizationGoal = {
  CONVERSIONS: 'CONVERSIONS',
  LANDING_PAGE_VIEWS: 'LANDING_PAGE_VIEWS',
  LINK_CLICKS: 'LINK_CLICKS',
  IMPRESSIONS: 'IMPRESSIONS',
  REACH: 'REACH',
  VALUE: 'VALUE',
} as const;
export type OptimizationGoal = (typeof OptimizationGoal)[keyof typeof OptimizationGoal];

export const optimizationGoalLabels: LabelMap<OptimizationGoal> = {
  CONVERSIONS: { ru: ['Конверсии'], en: ['Conversions'] },
  LANDING_PAGE_VIEWS: { ru: ['Просмотры целевой страницы'], en: ['Landing page views'] },
  LINK_CLICKS: { ru: ['Клики по ссылке'], en: ['Link clicks'] },
  IMPRESSIONS: { ru: ['Показы'], en: ['Impressions'] },
  REACH: { ru: ['Охват'], en: ['Reach'] },
  VALUE: { ru: ['Ценность'], en: ['Value'] },
};
```

---

### Task 5: `enums/attribution.ts`

```typescript
import type { LabelMap } from './index.js';

export const AttributionWindow = {
  CLICK_1D: 'CLICK_1D',
  CLICK_7D: 'CLICK_7D',
  CLICK_7D_VIEW_1D: 'CLICK_7D_VIEW_1D',
  CLICK_1D_VIEW_1D: 'CLICK_1D_VIEW_1D',
} as const;
export type AttributionWindow = (typeof AttributionWindow)[keyof typeof AttributionWindow];

export const attributionLabels: LabelMap<AttributionWindow> = {
  CLICK_1D: { ru: ['Клик 1 день', '1 день после клика'], en: ['1-day click'] },
  CLICK_7D: { ru: ['Клик 7 дней', '7 дней после клика'], en: ['7-day click'] },
  CLICK_7D_VIEW_1D: { ru: ['Клик 7 дней или просмотр 1 день'], en: ['7-day click or 1-day view'] },
  CLICK_1D_VIEW_1D: { ru: ['Клик 1 день или просмотр 1 день'], en: ['1-day click or 1-day view'] },
};
```

---

### Task 6: `enums/cta.ts`

```typescript
import type { LabelMap } from './index.js';

export const CallToAction = {
  LEARN_MORE: 'LEARN_MORE',
  SIGN_UP: 'SIGN_UP',
  SHOP_NOW: 'SHOP_NOW',
  SUBSCRIBE: 'SUBSCRIBE',
  GET_OFFER: 'GET_OFFER',
  BOOK_TRAVEL: 'BOOK_TRAVEL',
  DOWNLOAD: 'DOWNLOAD',
  CONTACT_US: 'CONTACT_US',
  APPLY_NOW: 'APPLY_NOW',
} as const;
export type CallToAction = (typeof CallToAction)[keyof typeof CallToAction];

export const ctaLabels: LabelMap<CallToAction> = {
  LEARN_MORE: { ru: ['Подробнее'], en: ['Learn more'] },
  SIGN_UP: { ru: ['Зарегистрироваться'], en: ['Sign up'] },
  SHOP_NOW: { ru: ['В магазин'], en: ['Shop now'] },
  SUBSCRIBE: { ru: ['Подписаться'], en: ['Subscribe'] },
  GET_OFFER: { ru: ['Получить предложение'], en: ['Get offer'] },
  BOOK_TRAVEL: { ru: ['Забронировать'], en: ['Book travel', 'Book now'] },
  DOWNLOAD: { ru: ['Скачать'], en: ['Download'] },
  CONTACT_US: { ru: ['Связаться с нами'], en: ['Contact us'] },
  APPLY_NOW: { ru: ['Подать заявку'], en: ['Apply now'] },
};
```

---

### Task 7: `enums/objective.ts`

```typescript
import type { LabelMap } from './index.js';

export const Objective = {
  SALES: 'SALES',
  LEADS: 'LEADS',
  ENGAGEMENT: 'ENGAGEMENT',
  TRAFFIC: 'TRAFFIC',
  AWARENESS: 'AWARENESS',
  APP_PROMOTION: 'APP_PROMOTION',
} as const;
export type Objective = (typeof Objective)[keyof typeof Objective];

export const objectiveLabels: LabelMap<Objective> = {
  SALES: { ru: ['Продажи'], en: ['Sales'] },
  LEADS: { ru: ['Лиды'], en: ['Leads'] },
  ENGAGEMENT: { ru: ['Вовлечённость'], en: ['Engagement'] },
  TRAFFIC: { ru: ['Трафик'], en: ['Traffic'] },
  AWARENESS: { ru: ['Узнаваемость'], en: ['Awareness'] },
  APP_PROMOTION: { ru: ['Продвижение приложения'], en: ['App promotion'] },
};
```

---

### Task 8: `enums/currency.ts`, `enums/placement.ts`

```typescript
// currency.ts
export const Currency = { USD: 'USD', EUR: 'EUR', RUB: 'RUB', UAH: 'UAH' } as const;
export type Currency = (typeof Currency)[keyof typeof Currency];

// placement.ts
import type { LabelMap } from './index.js';
export const Placement = { ADVANTAGE_PLUS: 'ADVANTAGE_PLUS', MANUAL: 'MANUAL' } as const;
export type Placement = (typeof Placement)[keyof typeof Placement];
export const placementLabels: LabelMap<Placement> = {
  ADVANTAGE_PLUS: { ru: ['Advantage+ плейсменты', 'Advantage+'], en: ['Advantage+ placements'] },
  MANUAL: { ru: ['Ручные плейсменты'], en: ['Manual placements'] },
};
```

---

### Task 9: Commit enums

- [ ] **Step 1:** `npm test` → все enum-тесты PASS.
- [ ] **Step 2: Commit**

```bash
git add services/browser-agent/src/creator/enums/
git commit -m "feat(creator): canonical enums + labelMap (ru/en) for all dropdown steps"
```

---

## Шаги — детальные задачи

Дальше следуют bite-sized задачи для каждого шага. Все шаги-выбиралки используют `selectValue`/`readSelectedValue`, поэтому код их `detect/isSatisfied/execute` минимальный.

### Шаблон: enum-шаг (применить к set_conversion_location, set_pixel_event.event, set_optimization_goal, set_attribution, set_cta)

Каждый такой шаг = ~30 строк кода. Один файл, один тест.

### Task 10: `steps/set_conversion_location.ts`

- [ ] **Step 1: Test** `steps/set_conversion_location.test.ts`:

```typescript
import { describe, it } from 'node:test';
import assert from 'node:assert';
import { SetConversionLocationStep } from './set_conversion_location.js';
import { ConversionLocation } from '../enums/index.js';

describe('SetConversionLocationStep', () => {
  it('isSatisfied true когда current === input.value', () => {
    const s = new SetConversionLocationStep();
    assert.equal(
      s.isSatisfied({ kind: 'matched', current: ConversionLocation.WEBSITE }, { value: ConversionLocation.WEBSITE }),
      true,
    );
  });

  it('isSatisfied false при отличии', () => {
    const s = new SetConversionLocationStep();
    assert.equal(
      s.isSatisfied({ kind: 'matched', current: ConversionLocation.APP }, { value: ConversionLocation.WEBSITE }),
      false,
    );
  });
});
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement** `steps/set_conversion_location.ts`:

```typescript
import { BaseStep } from './base.js';
import type { PlanContext, StepState } from '../types.js';
import { ConversionLocation, conversionLocationLabels } from '../enums/index.js';
import { readSelectedValue, selectValue, DropdownSpec } from './_helpers/select-from-dropdown.js';

const SPEC: DropdownSpec<ConversionLocation> = {
  block: {
    testid: 'conversion-location',
    aria: ['Место конверсии', 'Conversion location'],
    text: ['место конверсии', 'conversion location'],
  },
  labels: conversionLocationLabels,
};

export class SetConversionLocationStep extends BaseStep<{ value: ConversionLocation }, void> {
  name = 'set_conversion_location';

  async detect(_ctx: PlanContext): Promise<StepState> {
    const current = await readSelectedValue(SPEC);
    return current
      ? { kind: 'matched', current }
      : { kind: 'missing' };
  }

  isSatisfied(state: StepState, input: { value: ConversionLocation }): boolean {
    return state.kind === 'matched' && state.current === input.value;
  }

  protected async run(_state: StepState, input: { value: ConversionLocation }): Promise<void> {
    await selectValue(SPEC, input.value);
  }
}
```

- [ ] **Step 4: Run** → PASS.

- [ ] **Step 5: Commit**

```bash
git add services/browser-agent/src/creator/steps/set_conversion_location*.ts
git commit -m "feat(creator): step set_conversion_location"
```

---

### Task 11–14: enum-шаги по образцу Task 10

Повторить структуру Task 10 для следующих шагов (каждый = отдельный коммит):

- [ ] **Task 11:** `set_pixel_event.ts` — два под-действия (выбор пикселя по id через `humanType` в поиск + выбор event через `selectValue`).
- [ ] **Task 12:** `set_optimization_goal.ts` — SPEC.block `{ aria: ['Цель оптимизации', 'Optimization goal'] }`, labels `optimizationGoalLabels`.
- [ ] **Task 13:** `set_attribution.ts` — SPEC.block `{ aria: ['Окно атрибуции', 'Attribution setting'] }`, labels `attributionLabels`.
- [ ] **Task 14:** `set_cta.ts` — SPEC.block `{ aria: ['Призыв к действию', 'Call to action'] }`, labels `ctaLabels`.

Каждая задача: failing test → implementation → passing test → commit с сообщением `feat(creator): step <name>`.

---

### Task 15: `steps/set_geo.ts` (multi-select autocomplete)

- [ ] **Step 1: Test** `steps/set_geo.test.ts`:

```typescript
import { describe, it } from 'node:test';
import assert from 'node:assert';
import { SetGeoStep } from './set_geo.js';

describe('SetGeoStep', () => {
  it('isSatisfied когда current содержит все требуемые страны', () => {
    const s = new SetGeoStep();
    assert.equal(s.isSatisfied({ kind: 'matched', current: ['DE', 'AT'] } as any, { countries: ['DE'] }), true);
    assert.equal(s.isSatisfied({ kind: 'matched', current: ['DE'] } as any, { countries: ['DE', 'AT'] }), false);
  });
});
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement** `steps/set_geo.ts`:

```typescript
import { BaseStep } from './base.js';
import type { PlanContext, StepState } from '../types.js';
import { findBlock } from '../locator.js';
import { humanClick, humanType, humanIdle, IdleRange } from '../humanizer.js';

const BLOCK = {
  testid: 'locations',
  aria: ['Места', 'Locations'],
  text: ['места', 'locations'],
};

function readCurrentCountries(): string[] {
  const block = findBlock(BLOCK);
  if (!block) return [];
  return Array.from(block.querySelectorAll('[data-testid="selected-country"], [aria-label^="Удалить"]'))
    .map((el) => (el.getAttribute('data-country') || el.textContent || '').trim())
    .filter(Boolean);
}

export class SetGeoStep extends BaseStep<{ countries: string[] }, void> {
  name = 'set_geo';

  detect(): StepState {
    return { kind: 'matched', current: readCurrentCountries() };
  }

  isSatisfied(state: StepState, input: { countries: string[] }): boolean {
    const cur = new Set((state.current as string[]) || []);
    return input.countries.every((c) => cur.has(c));
  }

  protected async run(_s: StepState, input: { countries: string[] }): Promise<void> {
    const block = findBlock(BLOCK);
    if (!block) throw new Error('Блок Locations не найден');
    const search = block.querySelector<HTMLInputElement>('input[type="text"], input[type="search"]');
    if (!search) throw new Error('Поле поиска стран не найдено');
    const cur = new Set(readCurrentCountries());
    for (const code of input.countries) {
      if (cur.has(code)) continue;
      await humanType(search, code);
      await humanIdle(IdleRange.BETWEEN_STEPS);
      const option = block.querySelector<HTMLElement>(`[role="option"][data-country="${code}"], [role="option"]`);
      if (!option) throw new Error(`Страна ${code} не найдена в подсказках`);
      await humanClick(option);
      await humanIdle(IdleRange.SHORT);
    }
  }
}
```

- [ ] **Step 4: Run** → PASS.

- [ ] **Step 5: Commit**

```bash
git add services/browser-agent/src/creator/steps/set_geo*.ts
git commit -m "feat(creator): step set_geo (multi-country with autocomplete)"
```

---

### Task 16: `steps/set_age.ts`

- [ ] **Step 1: Test**:

```typescript
import { SetAgeStep } from './set_age.js';
import assert from 'node:assert';
import { describe, it } from 'node:test';

describe('SetAgeStep', () => {
  it('isSatisfied при совпадении диапазона', () => {
    const s = new SetAgeStep();
    assert.equal(s.isSatisfied({ kind: 'matched', current: { min: 18, max: 65 } } as any, { min: 18, max: 65 }), true);
    assert.equal(s.isSatisfied({ kind: 'matched', current: { min: 18, max: 65 } } as any, { min: 25, max: 45 }), false);
  });
});
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement** `steps/set_age.ts`:

```typescript
import { BaseStep } from './base.js';
import type { PlanContext, StepState } from '../types.js';
import { findBlock } from '../locator.js';
import { humanClick, humanType, humanIdle, IdleRange } from '../humanizer.js';

const BLOCK = { aria: ['Возраст', 'Age'], text: ['возраст', 'age'] };

function readRange(): { min: number; max: number } | null {
  const block = findBlock(BLOCK);
  if (!block) return null;
  const minSel = block.querySelector<HTMLElement>('[data-testid="age-min"] [aria-label]') ?? block.querySelector('select[name*="min"]');
  const maxSel = block.querySelector<HTMLElement>('[data-testid="age-max"] [aria-label]') ?? block.querySelector('select[name*="max"]');
  const min = Number((minSel?.getAttribute('aria-label') || (minSel as HTMLSelectElement)?.value || '').match(/\d+/)?.[0] || NaN);
  const max = Number((maxSel?.getAttribute('aria-label') || (maxSel as HTMLSelectElement)?.value || '').match(/\d+/)?.[0] || NaN);
  return Number.isFinite(min) && Number.isFinite(max) ? { min, max } : null;
}

async function pickFromDropdown(trigger: Element, value: number): Promise<void> {
  await humanClick(trigger);
  await humanIdle(IdleRange.SHORT);
  const option = Array.from(document.querySelectorAll<HTMLElement>('[role="option"]'))
    .find((el) => (el.textContent || '').trim() === String(value));
  if (!option) throw new Error(`Опция ${value} не найдена`);
  await humanClick(option);
}

export class SetAgeStep extends BaseStep<{ min: number; max: number }, void> {
  name = 'set_age';

  detect(): StepState {
    const cur = readRange();
    return cur ? { kind: 'matched', current: cur } : { kind: 'missing' };
  }

  isSatisfied(state: StepState, input: { min: number; max: number }): boolean {
    const c = state.current as { min: number; max: number } | undefined;
    return !!c && c.min === input.min && c.max === input.max;
  }

  protected async run(_s: StepState, input: { min: number; max: number }): Promise<void> {
    const block = findBlock(BLOCK);
    if (!block) throw new Error('Блок Age не найден');
    const minTrigger = block.querySelector<HTMLElement>('[data-testid="age-min"] button, button[aria-label*="мин"], button[aria-label*="min"]');
    const maxTrigger = block.querySelector<HTMLElement>('[data-testid="age-max"] button, button[aria-label*="макс"], button[aria-label*="max"]');
    if (!minTrigger || !maxTrigger) throw new Error('Триггеры возраста не найдены');
    await pickFromDropdown(minTrigger, input.min);
    await humanIdle(IdleRange.BETWEEN_STEPS);
    await pickFromDropdown(maxTrigger, input.max);
  }
}
```

- [ ] **Step 4: Run** → PASS.

- [ ] **Step 5: Commit**

```bash
git add services/browser-agent/src/creator/steps/set_age*.ts
git commit -m "feat(creator): step set_age (min/max dropdowns)"
```

---

### Task 17: `steps/set_budget.ts`

- [ ] **Step 1: Test**:

```typescript
import { SetBudgetStep } from './set_budget.js';
import { Currency } from '../enums/index.js';
import assert from 'node:assert';
import { describe, it } from 'node:test';

describe('SetBudgetStep', () => {
  it('isSatisfied при равной сумме', () => {
    const s = new SetBudgetStep();
    assert.equal(s.isSatisfied({ kind: 'matched', current: { amount: 50, currency: 'USD' } } as any, { amount: 50, currency: Currency.USD }), true);
  });
});
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement** `steps/set_budget.ts`:

```typescript
import { BaseStep } from './base.js';
import type { PlanContext, StepState } from '../types.js';
import { findBlock } from '../locator.js';
import { humanClick, humanType, humanIdle, IdleRange } from '../humanizer.js';
import type { Currency } from '../enums/index.js';

const BLOCK = { aria: ['Бюджет', 'Budget'], text: ['бюджет', 'budget'] };

interface BudgetInput { amount: number; currency: Currency }

function readAmount(): { amount: number; currency: string } | null {
  const block = findBlock(BLOCK);
  if (!block) return null;
  const input = block.querySelector<HTMLInputElement>('input[inputmode="decimal"], input[type="number"], input[name*="budget"]');
  if (!input) return null;
  const num = Number(input.value.replace(/[^\d.,]/g, '').replace(',', '.'));
  const cur = (block.querySelector<HTMLElement>('[aria-label*="валют"], [aria-label*="curren"]')?.textContent || '').trim();
  return Number.isFinite(num) ? { amount: num, currency: cur } : null;
}

export class SetBudgetStep extends BaseStep<BudgetInput, void> {
  name = 'set_budget';

  detect(): StepState {
    const cur = readAmount();
    return cur ? { kind: 'matched', current: cur } : { kind: 'missing' };
  }

  isSatisfied(state: StepState, input: BudgetInput): boolean {
    const c = state.current as { amount: number; currency: string } | undefined;
    return !!c && c.amount === input.amount;
  }

  protected async run(_s: StepState, input: BudgetInput): Promise<void> {
    const block = findBlock(BLOCK);
    if (!block) throw new Error('Блок Budget не найден');
    const field = block.querySelector<HTMLInputElement>('input[inputmode="decimal"], input[type="number"], input[name*="budget"]');
    if (!field) throw new Error('Поле бюджета не найдено');
    await humanClick(field);
    field.select();
    await humanIdle(IdleRange.SHORT);
    await humanType(field, String(input.amount));
    await humanIdle(IdleRange.BETWEEN_STEPS);
  }
}
```

- [ ] **Step 4: Run** → PASS.

- [ ] **Step 5: Commit** `feat(creator): step set_budget`.

---

### Task 18: `steps/set_schedule_start.ts`

- [ ] **Test + Implement** по образцу `set_budget` (поле date/time, проверяем что текущая дата равна `input.isoDate`).

```typescript
// detect: читает value у input[type="datetime-local"]
// isSatisfied: сравнивает ISO-строки
// run: humanClick поле → humanType isoDate → blur
```

- [ ] **Commit:** `feat(creator): step set_schedule_start`.

---

### Task 19: `steps/set_tracking_url.ts`

- [ ] **Test + Implement** — block `{ aria: ['URL для отслеживания', 'Tracking URL'] }`, простой textbox, humanType.
- [ ] **Commit:** `feat(creator): step set_tracking_url`.

---

### Task 20: `steps/fill_texts.ts`

- [ ] **Test:** isSatisfied когда все 3 поля уже содержат нужные значения.
- [ ] **Implement:** находит блоки `{ aria: ['Основной текст', 'Primary text'] }`, `{ aria: ['Заголовок', 'Headline'] }`, `{ aria: ['Описание', 'Description'] }`. Для каждого — humanClick → select all → humanType.
- [ ] **Commit:** `feat(creator): step fill_texts`.

---

### Task 21: `steps/upload_creatives.ts`

- [ ] **Test:** isSatisfied когда `block.querySelectorAll('[data-testid="creative-thumb"]').length === input.paths.length`.
- [ ] **Implement:** находит блок Media, ищет `<input type="file">`, вызывает emit `request_upload` с путями (Python подаёт через `page.locator(...).setInputFiles()` в Phase 4). До тех пор — шаг падает с понятной ошибкой если binding не реализован.

```typescript
import { BaseStep } from './base.js';
import type { PlanContext, StepState } from '../types.js';
import { findBlock } from '../locator.js';

const BLOCK = { aria: ['Медиа', 'Media'], text: ['медиа', 'media'] };

export class UploadCreativesStep extends BaseStep<{ paths: string[] }, void> {
  name = 'upload_creatives';

  detect(): StepState {
    const block = findBlock(BLOCK);
    const thumbs = block?.querySelectorAll('[data-testid="creative-thumb"]') ?? [];
    return { kind: 'matched', current: thumbs.length };
  }

  isSatisfied(state: StepState, input: { paths: string[] }): boolean {
    return (state.current as number) === input.paths.length;
  }

  protected async run(_s: StepState, input: { paths: string[] }, ctx: PlanContext): Promise<void> {
    const block = findBlock(BLOCK);
    if (!block) throw new Error('Блок Media не найден');
    const fileInput = block.querySelector<HTMLInputElement>('input[type="file"]');
    if (!fileInput) throw new Error('input[type=file] не найден в блоке Media');
    const id = `upload-${Date.now()}`;
    fileInput.setAttribute('data-fb-upload-id', id);
    ctx.emit('request_upload', { id, paths: input.paths, selector: `input[data-fb-upload-id="${id}"]` });
    // executor.runPlan делает humanIdle между шагами; Python увидит request_upload, выполнит setInputFiles, эмитит upload_done.
  }
}
```

- [ ] **Commit:** `feat(creator): step upload_creatives (emits request_upload)`.

---

### Task 22: `steps/create_campaign.ts`, `create_adset.ts`

- [ ] **Test:** isSatisfied когда текущая страница уже на нужном шаге wizard (по `data-testid="wizard-step"` или URL).
- [ ] **Implement:** клик «Создать», ввод name через humanType, выбор objective через selectValue.
- [ ] **Commit:** `feat(creator): step create_campaign`, `feat(creator): step create_adset`.

---

### Task 23: `duplicate_adset.ts`, `duplicate_ad.ts`, `rename_adset.ts`, `rename_ad.ts`, `reattach_creative.ts`, `switch_to_adset.ts`

Каждый = отдельная bite-задача с failing test (isSatisfied/edge case) → implement → commit. Шаблон:

```typescript
// test
import { DuplicateAdsetStep } from './duplicate_adset.js';
// проверяем что isSatisfied true когда в списке адсетов уже есть newName

// implement
// detect: читает список адсетов из левой панели по testid
// execute: правый клик/три точки → "Дублировать" → ввести имя → подтвердить
```

Один шаг = один файл = один тест = один commit `feat(creator): step <name>`.

---

### Task 24: `click_next.ts`, `save_draft.ts`

- [ ] **click_next:** detect возвращает `kind: 'matched'` всегда, isSatisfied всегда false (это переходный шаг). execute = `humanClick` по кнопке `aria: ['Далее', 'Next']`.
- [ ] **save_draft:** detect проверяет наличие индикатора «Сохранено», isSatisfied true если индикатор уже есть. execute = `humanClick` по кнопке `aria: ['Сохранить черновик', 'Save draft']`.
- [ ] Каждый отдельным commit.

---

### Task 25: `unknown.ts` — placeholder для нераспознанных шагов

- [ ] **Implement** `steps/unknown.ts`:

```typescript
import { BaseStep } from './base.js';
import type { PlanContext, StepState } from '../types.js';

export class UnknownStep extends BaseStep<{ raw: unknown }, never> {
  name = 'unknown';
  detect(): StepState { return { kind: 'unknown' }; }
  isSatisfied(): boolean { return false; }
  protected async run(_s: StepState, input: { raw: unknown }): Promise<never> {
    throw new Error(`UnimplementedStepError: запиши новый шаг для raw=${JSON.stringify(input.raw)}`);
  }
}
```

- [ ] **Commit:** `feat(creator): step unknown (fails with descriptive error)`.

---

### Task 26: `steps/index.ts` — регистрация всех шагов

- [ ] **Implement** `steps/index.ts`:

```typescript
import { registerStep } from '../registry.js';
import { SetConversionLocationStep } from './set_conversion_location.js';
import { SetPixelEventStep } from './set_pixel_event.js';
import { SetOptimizationGoalStep } from './set_optimization_goal.js';
import { SetAttributionStep } from './set_attribution.js';
import { SetCtaStep } from './set_cta.js';
import { SetGeoStep } from './set_geo.js';
import { SetAgeStep } from './set_age.js';
import { SetBudgetStep } from './set_budget.js';
import { SetScheduleStartStep } from './set_schedule_start.js';
import { SetTrackingUrlStep } from './set_tracking_url.js';
import { FillTextsStep } from './fill_texts.js';
import { UploadCreativesStep } from './upload_creatives.js';
import { CreateCampaignStep } from './create_campaign.js';
import { CreateAdsetStep } from './create_adset.js';
import { DuplicateAdsetStep } from './duplicate_adset.js';
import { DuplicateAdStep } from './duplicate_ad.js';
import { RenameAdsetStep } from './rename_adset.js';
import { RenameAdStep } from './rename_ad.js';
import { ReattachCreativeStep } from './reattach_creative.js';
import { SwitchToAdsetStep } from './switch_to_adset.js';
import { ClickNextStep } from './click_next.js';
import { SaveDraftStep } from './save_draft.js';
import { UnknownStep } from './unknown.js';

const STEPS = [
  new SetConversionLocationStep(),
  new SetPixelEventStep(),
  new SetOptimizationGoalStep(),
  new SetAttributionStep(),
  new SetCtaStep(),
  new SetGeoStep(),
  new SetAgeStep(),
  new SetBudgetStep(),
  new SetScheduleStartStep(),
  new SetTrackingUrlStep(),
  new FillTextsStep(),
  new UploadCreativesStep(),
  new CreateCampaignStep(),
  new CreateAdsetStep(),
  new DuplicateAdsetStep(),
  new DuplicateAdStep(),
  new RenameAdsetStep(),
  new RenameAdStep(),
  new ReattachCreativeStep(),
  new SwitchToAdsetStep(),
  new ClickNextStep(),
  new SaveDraftStep(),
  new UnknownStep(),
];

for (const s of STEPS) registerStep(s);

export { STEPS };
```

- [ ] **Test** `steps/index.test.ts`:

```typescript
import { describe, it } from 'node:test';
import assert from 'node:assert';
import './index.js';
import { listSteps } from '../registry.js';

describe('steps/index', () => {
  it('регистрирует все 23 шага', () => {
    assert.equal(listSteps().length, 23);
  });
});
```

- [ ] **Step 4: Run** → PASS.

- [ ] **Commit:** `feat(creator): register all steps in creator/steps/index.ts`.

---

### Task 27: Подключить регистрацию в `creator/index.ts`

- [ ] **Modify** `creator/index.ts` — добавить в начало:

```typescript
import './steps/index.js';
```

- [ ] **Commit:** `feat(creator): auto-register all steps on bundle load`.

---

### Task 28: End-of-phase smoke

- [ ] `cd services/browser-agent && npm run build && npm test` → all green.
- [ ] Проверить размер `dist/creator/index.js` (~50-150KB ожидается).

---

## Готово к Phase 4 когда

- Все 23 шага реализованы и зарегистрированы.
- Все enum'ы покрыты тестами.
- `npm test` зелёный.
- Бандл собирается без ошибок.
