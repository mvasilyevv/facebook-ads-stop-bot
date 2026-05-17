// Шаг: выбор цели оптимизации (Optimization goal).
import { BaseStep } from './base.js';
import type { PlanContext, StepState } from '../types.js';
import { OptimizationGoal, optimizationGoalLabels } from '../enums/index.js';
import {
  readSelectedValue,
  selectValue,
  type DropdownSpec,
} from './_helpers/select-from-dropdown.js';

const SPEC: DropdownSpec<OptimizationGoal> = {
  block: {
    testid: 'optimization-goal',
    aria: ['Цель оптимизации', 'Optimization goal', 'Performance goal'],
    text: ['цель оптимизации', 'optimization goal'],
  },
  labels: optimizationGoalLabels,
};

export class SetOptimizationGoalStep extends BaseStep<
  { value: OptimizationGoal },
  void
> {
  name = 'set_optimization_goal';

  async detect(_ctx: PlanContext): Promise<StepState> {
    const current = readSelectedValue(SPEC);
    return current ? { kind: 'matched', current } : { kind: 'missing' };
  }

  isSatisfied(state: StepState, input: { value: OptimizationGoal }): boolean {
    return state.kind === 'matched' && state.current === input.value;
  }

  protected async run(
    _state: StepState,
    input: { value: OptimizationGoal },
  ): Promise<void> {
    await selectValue(SPEC, input.value);
  }
}
