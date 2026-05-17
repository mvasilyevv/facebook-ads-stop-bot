import { BaseStep } from './base.js';
import type { PlanContext, StepState } from '../types.js';
interface ReattachInput {
    adName: string;
    paths: string[];
}
export declare class ReattachCreativeStep extends BaseStep<ReattachInput, void> {
    name: string;
    detect(): StepState;
    isSatisfied(state: StepState, input: ReattachInput): boolean;
    protected run(_s: StepState, input: ReattachInput, ctx: PlanContext): Promise<void>;
}
export {};
//# sourceMappingURL=reattach_creative.d.ts.map