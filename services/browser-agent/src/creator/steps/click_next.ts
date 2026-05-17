// Шаг: кнопка «Далее» / «Next» в wizard. Переходный, никогда не satisfied.
import { BaseStep } from './base.js';
import type { PlanContext, StepState } from '../types.js';
import { findByAriaLabel, findByNormalizedText } from '../locator.js';
import { humanClick, humanIdle, IdleRange } from '../humanizer.js';

export class ClickNextStep extends BaseStep<Record<string, never>, void> {
  name = 'click_next';

  detect(): StepState {
    return { kind: 'matched' };
  }

  isSatisfied(): boolean {
    return false;
  }

  protected async run(): Promise<void> {
    const btn =
      findByAriaLabel(['Далее', 'Next', 'Продолжить', 'Continue']) ??
      findByNormalizedText(['далее', 'next', 'продолжить', 'continue']);
    if (!btn) throw new Error('Кнопка «Далее» не найдена');
    await humanClick(btn);
    await humanIdle(IdleRange.BETWEEN_SCENES);
  }
}
