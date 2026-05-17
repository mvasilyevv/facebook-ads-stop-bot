// Шаг: ввод URL для отслеживания (Tracking URL parameters).
import { BaseStep } from './base.js';
import type { PlanContext, StepState } from '../types.js';
import { findBlock } from '../locator.js';
import { humanClick, humanType, humanIdle, IdleRange } from '../humanizer.js';

const BLOCK = {
  testid: 'tracking-url',
  aria: ['URL для отслеживания', 'Tracking URL', 'URL parameters'],
  text: ['url для отслеживания', 'tracking url'],
};

function readUrl(): string | null {
  const block = findBlock(BLOCK);
  if (!block) return null;
  const input = block.querySelector<HTMLInputElement | HTMLTextAreaElement>(
    'input[type="text"], input[type="url"], textarea',
  );
  return input?.value?.trim() || null;
}

export class SetTrackingUrlStep extends BaseStep<{ url: string }, void> {
  name = 'set_tracking_url';

  detect(): StepState {
    const cur = readUrl();
    return cur ? { kind: 'matched', current: cur } : { kind: 'missing' };
  }

  isSatisfied(state: StepState, input: { url: string }): boolean {
    return state.kind === 'matched' && state.current === input.url;
  }

  protected async run(_s: StepState, input: { url: string }): Promise<void> {
    const block = findBlock(BLOCK);
    if (!block) throw new Error('Блок Tracking URL не найден');
    const field = block.querySelector<HTMLInputElement | HTMLTextAreaElement>(
      'input[type="text"], input[type="url"], textarea',
    );
    if (!field) throw new Error('Поле URL не найдено');
    await humanClick(field);
    field.select();
    await humanIdle(IdleRange.SHORT);
    await humanType(field, input.url);
    await humanIdle(IdleRange.BETWEEN_STEPS);
  }
}
