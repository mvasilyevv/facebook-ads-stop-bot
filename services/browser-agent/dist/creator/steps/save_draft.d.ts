import { BaseStep } from './base.js';
import type { StepState } from '../types.js';
export declare class SaveDraftStep extends BaseStep<Record<string, never>, void> {
    name: string;
    detect(): StepState;
    isSatisfied(state: StepState): boolean;
    protected run(): Promise<void>;
}
//# sourceMappingURL=save_draft.d.ts.map