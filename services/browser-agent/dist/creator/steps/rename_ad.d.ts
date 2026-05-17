import { BaseStep } from './base.js';
import type { StepState } from '../types.js';
interface RenameInput {
    from: string;
    to: string;
}
export declare class RenameAdStep extends BaseStep<RenameInput, void> {
    name: string;
    detect(): StepState;
    isSatisfied(state: StepState, input: RenameInput): boolean;
    protected run(_s: StepState, input: RenameInput): Promise<void>;
}
export {};
//# sourceMappingURL=rename_ad.d.ts.map