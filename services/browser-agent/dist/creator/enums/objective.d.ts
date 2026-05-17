import type { LabelMap } from './index.js';
export declare const Objective: {
    readonly SALES: "SALES";
    readonly LEADS: "LEADS";
    readonly ENGAGEMENT: "ENGAGEMENT";
    readonly TRAFFIC: "TRAFFIC";
    readonly AWARENESS: "AWARENESS";
    readonly APP_PROMOTION: "APP_PROMOTION";
};
export type Objective = (typeof Objective)[keyof typeof Objective];
export declare const objectiveLabels: LabelMap<Objective>;
//# sourceMappingURL=objective.d.ts.map