import { BaseStep } from './base.js';
import type { PlanContext, StepState } from '../types.js';
import { CallToAction } from '../enums/index.js';
export declare class SetCtaStep extends BaseStep<{
    value: CallToAction;
}, void> {
    name: string;
    detect(_ctx: PlanContext): Promise<StepState>;
    isSatisfied(state: StepState, input: {
        value: CallToAction;
    }): boolean;
    protected run(_state: StepState, input: {
        value: CallToAction;
    }): Promise<void>;
}
//# sourceMappingURL=set_cta.d.ts.map