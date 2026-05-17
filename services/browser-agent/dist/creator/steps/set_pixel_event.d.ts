import { BaseStep } from './base.js';
import type { PlanContext, StepState } from '../types.js';
import { PixelEvent } from '../enums/index.js';
interface PixelEventInput {
    pixelId: string;
    event: PixelEvent;
}
export declare class SetPixelEventStep extends BaseStep<PixelEventInput, void> {
    name: string;
    detect(_ctx: PlanContext): Promise<StepState>;
    isSatisfied(state: StepState, input: PixelEventInput): boolean;
    protected run(_state: StepState, input: PixelEventInput): Promise<void>;
}
export {};
//# sourceMappingURL=set_pixel_event.d.ts.map