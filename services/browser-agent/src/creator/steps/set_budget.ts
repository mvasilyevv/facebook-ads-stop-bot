// Шаг: установка дневного/общего бюджета.
import { BaseStep } from './base.js';
import type { PlanContext, StepState } from '../types.js';
import { findBlock } from '../locator.js';
import { humanClick, humanType, humanIdle, IdleRange } from '../humanizer.js';
import type { Currency } from '../enums/index.js';

const BLOCK = { aria: ['Бюджет', 'Budget'], text: ['бюджет', 'budget'] };

interface BudgetInput {
  amount: number;
  currency: Currency;
}

function readAmount(): { amount: number; currency: string } | null {
  const block = findBlock(BLOCK);
  if (!block) return null;
  const input = block.querySelector<HTMLInputElement>(
    'input[inputmode="decimal"], input[type="number"], input[name*="budget"]',
  );
  if (!input) return null;
  const num = Number(input.value.replace(/[^\d.,]/g, '').replace(',', '.'));
  const cur =
    (block.querySelector<HTMLElement>('[aria-label*="валют"], [aria-label*="curren"]')
      ?.textContent || '').trim();
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
    if (!c || c.amount !== input.amount) return false;
    // Если currency задан явно — проверяем совпадение, иначе валюту игнорируем.
    if (input.currency && c.currency !== input.currency) return false;
    return true;
  }

  protected async run(_s: StepState, input: BudgetInput): Promise<void> {
    const block = findBlock(BLOCK);
    if (!block) throw new Error('Блок Budget не найден');
    const field = block.querySelector<HTMLInputElement>(
      'input[inputmode="decimal"], input[type="number"], input[name*="budget"]',
    );
    if (!field) throw new Error('Поле бюджета не найдено');
    await humanClick(field);
    field.select();
    await humanIdle(IdleRange.SHORT);
    await humanType(field, String(input.amount));
    await humanIdle(IdleRange.BETWEEN_STEPS);
  }
}
