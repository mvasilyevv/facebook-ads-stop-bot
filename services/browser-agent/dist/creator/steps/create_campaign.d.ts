import { BaseStep } from './base.js';
import type { StepState } from '../types.js';
import { Objective } from '../enums/index.js';
interface CreateCampaignInput {
    name: string;
    objective: Objective;
}
export declare class CreateCampaignStep extends BaseStep<CreateCampaignInput, void> {
    name: string;
    detect(): StepState;
    isSatisfied(state: StepState, input: CreateCampaignInput): boolean;
    protected run(_s: StepState, input: CreateCampaignInput): Promise<void>;
}
export {};
//# sourceMappingURL=create_campaign.d.ts.map