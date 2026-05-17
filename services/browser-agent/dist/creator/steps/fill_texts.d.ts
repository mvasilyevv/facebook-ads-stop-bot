import { BaseStep } from './base.js';
import type { StepState } from '../types.js';
interface FillTextsInput {
    primary: string;
    headline: string;
    description?: string;
}
export declare class FillTextsStep extends BaseStep<FillTextsInput, void> {
    name: string;
    detect(): StepState;
    isSatisfied(state: StepState, input: FillTextsInput): boolean;
    protected run(_s: StepState, input: FillTextsInput): Promise<void>;
}
export {};
//# sourceMappingURL=fill_texts.d.ts.map