import { BaseStep } from './base.js';
import type { StepState } from '../types.js';
export declare class CreateAdsetStep extends BaseStep<{
    name: string;
}, void> {
    name: string;
    detect(): StepState;
    isSatisfied(state: StepState, input: {
        name: string;
    }): boolean;
    protected run(_s: StepState, input: {
        name: string;
    }): Promise<void>;
}
//# sourceMappingURL=create_adset.d.ts.map