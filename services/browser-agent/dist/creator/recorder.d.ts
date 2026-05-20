import type { PlanStep } from './types.js';
export declare function startRecording(planName: string): void;
export declare function stopRecording(): {
    planName: string;
    steps: PlanStep[];
};
export declare function getStatus(): {
    active: boolean;
    planName: string;
    recordedSteps: number;
};
export declare function _resetRecorder(): void;
//# sourceMappingURL=recorder.d.ts.map