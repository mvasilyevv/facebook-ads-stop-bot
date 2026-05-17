import { BaseStep } from './base.js';
import type { StepState } from '../types.js';
export declare class SetGeoStep extends BaseStep<{
    countries: string[];
}, void> {
    name: string;
    detect(): StepState;
    isSatisfied(state: StepState, input: {
        countries: string[];
    }): boolean;
    protected run(_s: StepState, input: {
        countries: string[];
    }): Promise<void>;
}
//# sourceMappingURL=set_geo.d.ts.map