import { BaseStep } from './base.js';
import type { StepState } from '../types.js';
interface DuplicateAdsetInput {
    sourceName: string;
    newName: string;
}
export declare class DuplicateAdsetStep extends BaseStep<DuplicateAdsetInput, void> {
    name: string;
    detect(): StepState;
    isSatisfied(state: StepState, input: DuplicateAdsetInput): boolean;
    protected run(_s: StepState, input: DuplicateAdsetInput): Promise<void>;
}
export {};
//# sourceMappingURL=duplicate_adset.d.ts.map