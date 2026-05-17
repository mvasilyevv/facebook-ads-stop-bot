import { BaseStep } from './base.js';
import type { StepState } from '../types.js';
interface SwitchInput {
    name: string;
}
export declare class SwitchToAdsetStep extends BaseStep<SwitchInput, void> {
    name: string;
    detect(): StepState;
    isSatisfied(state: StepState, input: SwitchInput): boolean;
    protected run(_s: StepState, input: SwitchInput): Promise<void>;
}
export {};
//# sourceMappingURL=switch_to_adset.d.ts.map