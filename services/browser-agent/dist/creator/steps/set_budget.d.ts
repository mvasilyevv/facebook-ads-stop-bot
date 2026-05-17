import { BaseStep } from './base.js';
import type { StepState } from '../types.js';
import type { Currency } from '../enums/index.js';
interface BudgetInput {
    amount: number;
    currency: Currency;
}
export declare class SetBudgetStep extends BaseStep<BudgetInput, void> {
    name: string;
    detect(): StepState;
    isSatisfied(state: StepState, input: BudgetInput): boolean;
    protected run(_s: StepState, input: BudgetInput): Promise<void>;
}
export {};
//# sourceMappingURL=set_budget.d.ts.map