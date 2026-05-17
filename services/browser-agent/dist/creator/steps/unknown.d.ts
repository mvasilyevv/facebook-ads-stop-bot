import { BaseStep } from './base.js';
import type { PlanContext, StepState } from '../types.js';
export declare class UnknownStep extends BaseStep<{
    raw: unknown;
}, never> {
    name: string;
    detect(): StepState;
    isSatisfied(): boolean;
    protected run(_s: StepState, input: {
        raw: unknown;
    }, _ctx: PlanContext): Promise<never>;
}
//# sourceMappingURL=unknown.d.ts.map