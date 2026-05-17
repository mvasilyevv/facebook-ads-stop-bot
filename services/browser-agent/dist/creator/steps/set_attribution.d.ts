import { BaseStep } from './base.js';
import type { PlanContext, StepState } from '../types.js';
import { AttributionWindow } from '../enums/index.js';
export declare class SetAttributionStep extends BaseStep<{
    value: AttributionWindow;
}, void> {
    name: string;
    detect(_ctx: PlanContext): Promise<StepState>;
    isSatisfied(state: StepState, input: {
        value: AttributionWindow;
    }): boolean;
    protected run(_state: StepState, input: {
        value: AttributionWindow;
    }): Promise<void>;
}
//# sourceMappingURL=set_attribution.d.ts.map