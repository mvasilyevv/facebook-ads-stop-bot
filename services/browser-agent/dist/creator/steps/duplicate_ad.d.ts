import { BaseStep } from './base.js';
import type { StepState } from '../types.js';
interface DuplicateAdInput {
    sourceName: string;
    newName: string;
}
export declare class DuplicateAdStep extends BaseStep<DuplicateAdInput, void> {
    name: string;
    detect(): StepState;
    isSatisfied(state: StepState, input: DuplicateAdInput): boolean;
    protected run(_s: StepState, input: DuplicateAdInput): Promise<void>;
}
export {};
//# sourceMappingURL=duplicate_ad.d.ts.map