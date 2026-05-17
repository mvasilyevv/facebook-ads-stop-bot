// Шаг: дублирование объявления. Идемпотентен если в дереве уже есть newName.
import { BaseStep } from './base.js';
import type { PlanContext, StepState } from '../types.js';
import { findByAriaLabel, findByNormalizedText } from '../locator.js';
import { humanClick, humanType, humanIdle, IdleRange } from '../humanizer.js';
import { findTreeNodeByName, listTreeNodeNames } from './_helpers/tree-nav.js';

interface DuplicateAdInput {
  sourceName: string;
  newName: string;
}

export class DuplicateAdStep extends BaseStep<DuplicateAdInput, void> {
  name = 'duplicate_ad';

  detect(): StepState {
    return { kind: 'matched', current: listTreeNodeNames('ad') };
  }

  isSatisfied(state: StepState, input: DuplicateAdInput): boolean {
    const names = (state.current as string[]) || [];
    return names.includes(input.newName);
  }

  protected async run(_s: StepState, input: DuplicateAdInput): Promise<void> {
    const node = findTreeNodeByName('ad', input.sourceName);
    if (!node) throw new Error(`Объявление "${input.sourceName}" не найдено в дереве`);
    const menu =
      node.querySelector<HTMLElement>('button[aria-haspopup="menu"], [data-testid="row-menu"]') ??
      node;
    await humanClick(menu);
    await humanIdle(IdleRange.SHORT);
    const dup =
      findByAriaLabel(['Дублировать', 'Duplicate']) ??
      findByNormalizedText(['дублировать', 'duplicate']);
    if (!dup) throw new Error('Пункт меню «Дублировать» не найден');
    await humanClick(dup);
    await humanIdle(IdleRange.BETWEEN_STEPS);
    const nameInput = document.querySelector<HTMLInputElement>(
      'input[type="text"][name*="name"], [data-testid="duplicate-name"] input',
    );
    if (nameInput) {
      await humanClick(nameInput);
      nameInput.select();
      await humanIdle(IdleRange.SHORT);
      await humanType(nameInput, input.newName);
    }
    const confirm =
      findByAriaLabel(['Дублировать', 'Duplicate', 'Подтвердить', 'Confirm']) ??
      findByNormalizedText(['дублировать', 'duplicate', 'подтвердить', 'confirm']);
    if (confirm) {
      await humanClick(confirm);
      await humanIdle(IdleRange.BETWEEN_STEPS);
    }
  }
}
