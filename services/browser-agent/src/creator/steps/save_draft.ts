// Шаг: «Сохранить черновик». Идемпотентен если уже показан индикатор «Сохранено».
import { BaseStep } from './base.js';
import type { PlanContext, StepState } from '../types.js';
import { findByAriaLabel, findByNormalizedText } from '../locator.js';
import { humanClick, humanIdle, IdleRange } from '../humanizer.js';

function hasSavedIndicator(): boolean {
  const aria = findByAriaLabel(['Сохранено', 'Saved', 'Черновик сохранён']);
  if (aria) return true;
  const text = findByNormalizedText(['сохранено', 'saved', 'черновик сохранён']);
  return !!text;
}

export class SaveDraftStep extends BaseStep<Record<string, never>, void> {
  name = 'save_draft';

  detect(): StepState {
    return hasSavedIndicator()
      ? { kind: 'matched', current: 'saved' }
      : { kind: 'missing' };
  }

  isSatisfied(state: StepState): boolean {
    return state.kind === 'matched' && state.current === 'saved';
  }

  protected async run(): Promise<void> {
    const btn =
      findByAriaLabel(['Сохранить черновик', 'Save draft', 'Сохранить']) ??
      findByNormalizedText(['сохранить черновик', 'save draft']);
    if (!btn) throw new Error('Кнопка «Сохранить черновик» не найдена');
    await humanClick(btn);
    await humanIdle(IdleRange.BETWEEN_SCENES);
  }
}
