// Базовый класс шага: встроенная идемпотентность через detect()+isSatisfied(),
// потомки переопределяют только detect/isSatisfied/run.
import type { PlanContext, Step, StepState } from '../types.js';

export abstract class BaseStep<I = unknown, O = unknown> implements Step<I, O> {
  abstract name: string;
  abstract detect(ctx: PlanContext): Promise<StepState> | StepState;
  abstract isSatisfied(state: StepState, input: I): boolean;

  protected abstract run(state: StepState, input: I, ctx: PlanContext): Promise<O>;

  async execute(state: StepState, input: I, ctx: PlanContext): Promise<O> {
    if (this.isSatisfied(state, input)) {
      ctx.emit('step_skipped', { step: this.name, reason: 'already_satisfied' });
      return undefined as unknown as O;
    }
    return await this.run(state, input, ctx);
  }
}
