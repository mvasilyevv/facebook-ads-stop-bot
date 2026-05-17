import { BaseStep } from './base.js';
import type { PlanContext, StepState } from '../types.js';
import { OptimizationGoal } from '../enums/index.js';
export declare class SetOptimizationGoalStep extends BaseStep<{
    value: OptimizationGoal;
}, void> {
    name: string;
    detect(_ctx: PlanContext): Promise<StepState>;
    isSatisfied(state: StepState, input: {
        value: OptimizationGoal;
    }): boolean;
    protected run(_state: StepState, input: {
        value: OptimizationGoal;
    }): Promise<void>;
}
//# sourceMappingURL=set_optimization_goal.d.ts.map