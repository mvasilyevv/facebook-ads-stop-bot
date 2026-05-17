// Шаг: переключение на конкретный ad set в дереве слева.
import { BaseStep } from './base.js';
import type { PlanContext, StepState } from '../types.js';
import { humanClick, humanIdle, IdleRange } from '../humanizer.js';
import { findTreeNodeByName } from './_helpers/tree-nav.js';

interface SwitchInput {
  name: string;
}

function currentAdsetName(): string | null {
  const sel = document.querySelector<HTMLElement>(
    '[data-tree-role="adset"][aria-selected="true"], [data-testid="adset-node"][aria-current="true"]',
  );
  if (!sel) return null;
  return (sel.getAttribute('data-name') || sel.textContent || '').trim();
}

export class SwitchToAdsetStep extends BaseStep<SwitchInput, void> {
  name = 'switch_to_adset';

  detect(): StepState {
    const cur = currentAdsetName();
    return cur ? { kind: 'matched', current: cur } : { kind: 'missing' };
  }

  isSatisfied(state: StepState, input: SwitchInput): boolean {
    return state.kind === 'matched' && state.current === input.name;
  }

  protected async run(_s: StepState, input: SwitchInput): Promise<void> {
    const node = findTreeNodeByName('adset', input.name);
    if (!node) throw new Error(`Ad set "${input.name}" не найден в дереве`);
    await humanClick(node);
    await humanIdle(IdleRange.BETWEEN_STEPS);
  }
}
