import type { PlanContext, Step, StepState } from '../types.js';
export declare abstract class BaseStep<I = unknown, O = unknown> implements Step<I, O> {
    abstract name: string;
    abstract detect(ctx: PlanContext): Promise<StepState> | StepState;
    abstract isSatisfied(state: StepState, input: I): boolean;
    protected abstract run(state: StepState, input: I, ctx: PlanContext): Promise<O>;
    execute(state: StepState, input: I, ctx: PlanContext): Promise<O>;
}
//# sourceMappingURL=base.d.ts.map