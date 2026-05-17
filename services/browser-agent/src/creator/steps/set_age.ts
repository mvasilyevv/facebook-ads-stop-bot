// Шаг: установка диапазона возраста через два дропдауна (min, max).
import { BaseStep } from './base.js';
import type { PlanContext, StepState } from '../types.js';
import { findBlock } from '../locator.js';
import { humanClick, humanIdle, IdleRange } from '../humanizer.js';

const BLOCK = { aria: ['Возраст', 'Age'], text: ['возраст', 'age'] };

function parseAriaNum(el: Element | null): number {
  if (!el) return NaN;
  const raw = el.getAttribute('aria-label') ?? (el as HTMLSelectElement).value ?? '';
  const m = raw.match(/\d+/);
  return m ? Number(m[0]) : NaN;
}

function readRange(): { min: number; max: number } | null {
  const block = findBlock(BLOCK);
  if (!block) return null;
  const minSel =
    block.querySelector('[data-testid="age-min"] [aria-label]') ??
    block.querySelector('select[name*="min"]');
  const maxSel =
    block.querySelector('[data-testid="age-max"] [aria-label]') ??
    block.querySelector('select[name*="max"]');
  const min = parseAriaNum(minSel);
  const max = parseAriaNum(maxSel);
  return Number.isFinite(min) && Number.isFinite(max) ? { min, max } : null;
}

async function pickFromDropdown(trigger: Element, value: number): Promise<void> {
  await humanClick(trigger);
  await humanIdle(IdleRange.SHORT);
  const option = Array.from(
    document.querySelectorAll<HTMLElement>('[role="option"]'),
  ).find((el) => (el.textContent || '').trim() === String(value));
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
    const minTrigger = block.querySelector<HTMLElement>(
      '[data-testid="age-min"] button, button[aria-label*="мин"], button[aria-label*="min"]',
    );
    const maxTrigger = block.querySelector<HTMLElement>(
      '[data-testid="age-max"] button, button[aria-label*="макс"], button[aria-label*="max"]',
    );
    if (!minTrigger || !maxTrigger) throw new Error('Триггеры возраста не найдены');
    await pickFromDropdown(minTrigger, input.min);
    await humanIdle(IdleRange.BETWEEN_STEPS);
    await pickFromDropdown(maxTrigger, input.max);
  }
}
