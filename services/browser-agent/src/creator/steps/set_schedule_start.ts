// Шаг: задание даты/времени старта расписания (datetime-local input).
import { BaseStep } from './base.js';
import type { PlanContext, StepState } from '../types.js';
import { findBlock } from '../locator.js';
import { humanClick, humanType, humanIdle, IdleRange } from '../humanizer.js';

const BLOCK = {
  testid: 'schedule-start',
  aria: ['Дата начала', 'Start date', 'Schedule start'],
  text: ['дата начала', 'start date'],
};

function readStart(): string | null {
  const block = findBlock(BLOCK);
  if (!block) return null;
  const field = block.querySelector<HTMLInputElement>(
    'input[type="datetime-local"], input[type="date"], input[name*="start"]',
  );
  return field?.value?.trim() || null;
}

export class SetScheduleStartStep extends BaseStep<{ isoDate: string }, void> {
  name = 'set_schedule_start';

  detect(): StepState {
    const cur = readStart();
    return cur ? { kind: 'matched', current: cur } : { kind: 'missing' };
  }

  isSatisfied(state: StepState, input: { isoDate: string }): boolean {
    return state.kind === 'matched' && state.current === input.isoDate;
  }

  protected async run(_s: StepState, input: { isoDate: string }): Promise<void> {
    const block = findBlock(BLOCK);
    if (!block) throw new Error('Блок Schedule start не найден');
    const field = block.querySelector<HTMLInputElement>(
      'input[type="datetime-local"], input[type="date"], input[name*="start"]',
    );
    if (!field) throw new Error('Поле даты начала не найдено');
    await humanClick(field);
    field.select();
    await humanIdle(IdleRange.SHORT);
    await humanType(field, input.isoDate);
    await humanIdle(IdleRange.BETWEEN_STEPS);
  }
}
