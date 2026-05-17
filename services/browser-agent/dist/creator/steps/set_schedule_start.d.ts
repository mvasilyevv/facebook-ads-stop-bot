import { BaseStep } from './base.js';
import type { StepState } from '../types.js';
export declare class SetScheduleStartStep extends BaseStep<{
    isoDate: string;
}, void> {
    name: string;
    detect(): StepState;
    isSatisfied(state: StepState, input: {
        isoDate: string;
    }): boolean;
    protected run(_s: StepState, input: {
        isoDate: string;
    }): Promise<void>;
}
//# sourceMappingURL=set_schedule_start.d.ts.map