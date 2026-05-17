// Шаг: заполнение текстовых полей объявления (Primary text, Headline, Description).
import { BaseStep } from './base.js';
import type { PlanContext, StepState } from '../types.js';
import { findBlock, type BlockLookup } from '../locator.js';
import { humanClick, humanType, humanIdle, IdleRange } from '../humanizer.js';

interface FillTextsInput {
  primary: string;
  headline: string;
  description?: string;
}

const PRIMARY: BlockLookup = {
  testid: 'primary-text',
  aria: ['Основной текст', 'Primary text'],
};
const HEADLINE: BlockLookup = {
  testid: 'headline',
  aria: ['Заголовок', 'Headline'],
};
const DESCRIPTION: BlockLookup = {
  testid: 'description',
  aria: ['Описание', 'Description'],
};

function readField(block: BlockLookup): string | null {
  const el = findBlock(block);
  if (!el) return null;
  const input = el.querySelector<HTMLInputElement | HTMLTextAreaElement>(
    'textarea, input[type="text"], [contenteditable="true"]',
  );
  if (!input) return null;
  if ('value' in input && typeof input.value === 'string') return input.value;
  return (input.textContent || '').trim();
}

async function fillBlock(block: BlockLookup, value: string): Promise<void> {
  const el = findBlock(block);
  if (!el) throw new Error(`Блок не найден: ${JSON.stringify(block)}`);
  const field = el.querySelector<HTMLInputElement | HTMLTextAreaElement>(
    'textarea, input[type="text"]',
  );
  if (!field) throw new Error('Поле текста не найдено');
  await humanClick(field);
  field.select?.();
  await humanIdle(IdleRange.SHORT);
  await humanType(field, value);
  await humanIdle(IdleRange.BETWEEN_STEPS);
}

export class FillTextsStep extends BaseStep<FillTextsInput, void> {
  name = 'fill_texts';

  detect(): StepState {
    const current = {
      primary: readField(PRIMARY) ?? '',
      headline: readField(HEADLINE) ?? '',
      description: readField(DESCRIPTION) ?? '',
    };
    return { kind: 'matched', current };
  }

  isSatisfied(state: StepState, input: FillTextsInput): boolean {
    const c = state.current as
      | { primary: string; headline: string; description: string }
      | undefined;
    if (!c) return false;
    if (c.primary !== input.primary) return false;
    if (c.headline !== input.headline) return false;
    if (input.description !== undefined && c.description !== input.description)
      return false;
    return true;
  }

  protected async run(_s: StepState, input: FillTextsInput): Promise<void> {
    await fillBlock(PRIMARY, input.primary);
    await fillBlock(HEADLINE, input.headline);
    if (input.description) await fillBlock(DESCRIPTION, input.description);
  }
}
