// Шаг: мульти-выбор гео (страны) с автокомплитом.
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
  return Array.from(
    block.querySelectorAll('[data-testid="selected-country"], [aria-label^="Удалить"]'),
  )
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
    // Append-only by design — лишние страны не удаляем, чтобы не сломать ручные правки.
    return input.countries.every((c) => cur.has(c));
  }

  protected async run(_s: StepState, input: { countries: string[] }): Promise<void> {
    const block = findBlock(BLOCK);
    if (!block) throw new Error('Блок Locations не найден');
    const search = block.querySelector<HTMLInputElement>(
      'input[type="text"], input[type="search"]',
    );
    if (!search) throw new Error('Поле поиска стран не найдено');
    const cur = new Set(readCurrentCountries());
    for (const code of input.countries) {
      if (cur.has(code)) continue;
      await humanType(search, code);
      await humanIdle(IdleRange.BETWEEN_STEPS);
      const option =
        document.querySelector<HTMLElement>(`[role="option"][data-country="${code}"]`) ??
        document.querySelector<HTMLElement>('[role="option"]');
      if (!option) throw new Error(`Страна ${code} не найдена в подсказках`);
      await humanClick(option);
      await humanIdle(IdleRange.SHORT);
    }
  }
}
