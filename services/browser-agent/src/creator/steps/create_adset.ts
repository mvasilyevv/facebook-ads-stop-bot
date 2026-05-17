// Шаг: создание ad set (ввод имени в форме адсета).
import { BaseStep } from './base.js';
import type { PlanContext, StepState } from '../types.js';
import { findBlock } from '../locator.js';
import { humanClick, humanType, humanIdle, IdleRange } from '../humanizer.js';

const NAME_BLOCK = {
  testid: 'adset-name',
  aria: ['Название группы объявлений', 'Ad set name'],
};

function readName(): string | null {
  const block = findBlock(NAME_BLOCK);
  if (!block) return null;
  const input = block.querySelector<HTMLInputElement>('input[type="text"]');
  return input?.value || null;
}

export class CreateAdsetStep extends BaseStep<{ name: string }, void> {
  name = 'create_adset';

  detect(): StepState {
    const name = readName();
    return name ? { kind: 'matched', current: { name } } : { kind: 'missing' };
  }

  isSatisfied(state: StepState, input: { name: string }): boolean {
    const c = state.current as { name: string } | undefined;
    return !!c && c.name === input.name;
  }

  protected async run(_s: StepState, input: { name: string }): Promise<void> {
    const block = findBlock(NAME_BLOCK);
    if (!block) throw new Error('Блок Ad set name не найден');
    const field = block.querySelector<HTMLInputElement>('input[type="text"]');
    if (!field) throw new Error('Поле имени ad set не найдено');
    await humanClick(field);
    field.select();
    await humanIdle(IdleRange.SHORT);
    await humanType(field, input.name);
    await humanIdle(IdleRange.BETWEEN_STEPS);
  }
}
