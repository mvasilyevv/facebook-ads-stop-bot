import { BaseStep } from './base.js';
import type { StepState } from '../types.js';
export declare class ClickNextStep extends BaseStep<Record<string, never>, void> {
    name: string;
    detect(): StepState;
    isSatisfied(): boolean;
    protected run(): Promise<void>;
}
//# sourceMappingURL=click_next.d.ts.map