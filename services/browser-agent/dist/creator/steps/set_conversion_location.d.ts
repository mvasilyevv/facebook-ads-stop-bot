import { BaseStep } from './base.js';
import type { PlanContext, StepState } from '../types.js';
import { ConversionLocation } from '../enums/index.js';
export declare class SetConversionLocationStep extends BaseStep<{
    value: ConversionLocation;
}, void> {
    name: string;
    detect(_ctx: PlanContext): Promise<StepState>;
    isSatisfied(state: StepState, input: {
        value: ConversionLocation;
    }): boolean;
    protected run(_state: StepState, input: {
        value: ConversionLocation;
    }): Promise<void>;
}
//# sourceMappingURL=set_conversion_location.d.ts.map