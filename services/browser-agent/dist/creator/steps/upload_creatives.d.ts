import { BaseStep } from './base.js';
import type { PlanContext, StepState } from '../types.js';
export declare class UploadCreativesStep extends BaseStep<{
    paths: string[];
}, void> {
    name: string;
    detect(): StepState;
    isSatisfied(state: StepState, input: {
        paths: string[];
    }): boolean;
    protected run(_s: StepState, input: {
        paths: string[];
    }, ctx: PlanContext): Promise<void>;
}
//# sourceMappingURL=upload_creatives.d.ts.map