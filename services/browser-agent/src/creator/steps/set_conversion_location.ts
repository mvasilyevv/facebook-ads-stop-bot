// Шаг: выбор «Место конверсии» (Conversion Location).
import { BaseStep } from './base.js';
import type { PlanContext, StepState } from '../types.js';
import { ConversionLocation, conversionLocationLabels } from '../enums/index.js';
import {
  readSelectedValue,
  selectValue,
  type DropdownSpec,
} from './_helpers/select-from-dropdown.js';

const SPEC: DropdownSpec<ConversionLocation> = {
  block: {
    testid: 'conversion-location',
    aria: ['Место конверсии', 'Conversion location'],
    text: ['место конверсии', 'conversion location'],
  },
  labels: conversionLocationLabels,
};

export class SetConversionLocationStep extends BaseStep<
  { value: ConversionLocation },
  void
> {
  name = 'set_conversion_location';

  async detect(_ctx: PlanContext): Promise<StepState> {
    const current = readSelectedValue(SPEC);
    return current ? { kind: 'matched', current } : { kind: 'missing' };
  }

  isSatisfied(state: StepState, input: { value: ConversionLocation }): boolean {
    return state.kind === 'matched' && state.current === input.value;
  }

  protected async run(
    _state: StepState,
    input: { value: ConversionLocation },
  ): Promise<void> {
    await selectValue(SPEC, input.value);
  }
}
