// Шаг: переименование объявления (аналогично rename_adset, role=ad).
import { BaseStep } from './base.js';
import type { PlanContext, StepState } from '../types.js';
import { humanClick, humanType, humanIdle, IdleRange } from '../humanizer.js';
import { findTreeNodeByName, listTreeNodeNames } from './_helpers/tree-nav.js';

interface RenameInput {
  from: string;
  to: string;
}

export class RenameAdStep extends BaseStep<RenameInput, void> {
  name = 'rename_ad';

  detect(): StepState {
    return { kind: 'matched', current: listTreeNodeNames('ad') };
  }

  isSatisfied(state: StepState, input: RenameInput): boolean {
    const names = (state.current as string[]) || [];
    return names.includes(input.to) && !names.includes(input.from);
  }

  protected async run(_s: StepState, input: RenameInput): Promise<void> {
    const node = findTreeNodeByName('ad', input.from);
    if (!node) throw new Error(`Объявление "${input.from}" не найдено`);
    await humanClick(node);
    await humanIdle(IdleRange.SHORT);
    const rect = node.getBoundingClientRect();
    node.dispatchEvent(
      new MouseEvent('dblclick', {
        bubbles: true,
        clientX: rect.left + rect.width / 2,
        clientY: rect.top + rect.height / 2,
      }),
    );
    await humanIdle(IdleRange.SHORT);
    const input2 =
      node.querySelector<HTMLInputElement>('input[type="text"]') ??
      document.querySelector<HTMLInputElement>('[data-testid="rename-input"] input');
    if (!input2) throw new Error('Поле переименования не найдено');
    input2.select();
    await humanType(input2, input.to);
    await humanIdle(IdleRange.BETWEEN_STEPS);
  }
}
