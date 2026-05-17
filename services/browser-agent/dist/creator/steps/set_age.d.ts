import { BaseStep } from './base.js';
import type { StepState } from '../types.js';
export declare class SetAgeStep extends BaseStep<{
    min: number;
    max: number;
}, void> {
    name: string;
    detect(): StepState;
    isSatisfied(state: StepState, input: {
        min: number;
        max: number;
    }): boolean;
    protected run(_s: StepState, input: {
        min: number;
        max: number;
    }): Promise<void>;
}
//# sourceMappingURL=set_age.d.ts.map