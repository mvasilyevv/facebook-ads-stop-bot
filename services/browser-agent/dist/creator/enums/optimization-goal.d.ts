import type { LabelMap } from './index.js';
export declare const OptimizationGoal: {
    readonly CONVERSIONS: "CONVERSIONS";
    readonly LANDING_PAGE_VIEWS: "LANDING_PAGE_VIEWS";
    readonly LINK_CLICKS: "LINK_CLICKS";
    readonly IMPRESSIONS: "IMPRESSIONS";
    readonly REACH: "REACH";
    readonly VALUE: "VALUE";
};
export type OptimizationGoal = (typeof OptimizationGoal)[keyof typeof OptimizationGoal];
export declare const optimizationGoalLabels: LabelMap<OptimizationGoal>;
//# sourceMappingURL=optimization-goal.d.ts.map