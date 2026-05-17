// Шаг: выбор окна атрибуции (Attribution setting).
import { BaseStep } from './base.js';
import type { PlanContext, StepState } from '../types.js';
import { AttributionWindow, attributionLabels } from '../enums/index.js';
import {
  readSelectedValue,
  selectValue,
  type DropdownSpec,
} from './_helpers/select-from-dropdown.js';

const SPEC: DropdownSpec<AttributionWindow> = {
  block: {
    testid: 'attribution-setting',
    aria: ['Окно атрибуции', 'Attribution setting', 'Attribution window'],
    text: ['окно атрибуции', 'attribution'],
  },
  labels: attributionLabels,
};

export class SetAttributionStep extends BaseStep<{ value: AttributionWindow }, void> {
  name = 'set_attribution';

  async detect(_ctx: PlanContext): Promise<StepState> {
    const current = readSelectedValue(SPEC);
    return current ? { kind: 'matched', current } : { kind: 'missing' };
  }

  isSatisfied(state: StepState, input: { value: AttributionWindow }): boolean {
    return state.kind === 'matched' && state.current === input.value;
  }

  protected async run(
    _state: StepState,
    input: { value: AttributionWindow },
  ): Promise<void> {
    await selectValue(SPEC, input.value);
  }
}
