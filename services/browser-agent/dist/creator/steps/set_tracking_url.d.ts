import { BaseStep } from './base.js';
import type { StepState } from '../types.js';
export declare class SetTrackingUrlStep extends BaseStep<{
    url: string;
}, void> {
    name: string;
    detect(): StepState;
    isSatisfied(state: StepState, input: {
        url: string;
    }): boolean;
    protected run(_s: StepState, input: {
        url: string;
    }): Promise<void>;
}
//# sourceMappingURL=set_tracking_url.d.ts.map