// Шаг: выбор Call-To-Action в карточке объявления.
import { BaseStep } from './base.js';
import type { PlanContext, StepState } from '../types.js';
import { CallToAction, ctaLabels } from '../enums/index.js';
import {
  readSelectedValue,
  selectValue,
  type DropdownSpec,
} from './_helpers/select-from-dropdown.js';

const SPEC: DropdownSpec<CallToAction> = {
  block: {
    testid: 'call-to-action',
    aria: ['Призыв к действию', 'Call to action'],
    text: ['призыв к действию', 'call to action'],
  },
  labels: ctaLabels,
};

export class SetCtaStep extends BaseStep<{ value: CallToAction }, void> {
  name = 'set_cta';

  async detect(_ctx: PlanContext): Promise<StepState> {
    const current = readSelectedValue(SPEC);
    return current ? { kind: 'matched', current } : { kind: 'missing' };
  }

  isSatisfied(state: StepState, input: { value: CallToAction }): boolean {
    return state.kind === 'matched' && state.current === input.value;
  }

  protected async run(_state: StepState, input: { value: CallToAction }): Promise<void> {
    await selectValue(SPEC, input.value);
  }
}
