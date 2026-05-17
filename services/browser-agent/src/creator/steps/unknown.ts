// Placeholder для нераспознанных шагов — всегда падает с описательным сообщением.
import { BaseStep } from './base.js';
import type { PlanContext, StepState } from '../types.js';

export class UnknownStep extends BaseStep<{ raw: unknown }, never> {
  name = 'unknown';
  detect(): StepState {
    return { kind: 'unknown' };
  }
  isSatisfied(): boolean {
    return false;
  }
  protected async run(
    _s: StepState,
    input: { raw: unknown },
    _ctx: PlanContext,
  ): Promise<never> {
    throw new Error(
      `UnimplementedStepError: запиши новый шаг для raw=${JSON.stringify(input.raw)}`,
    );
  }
}
