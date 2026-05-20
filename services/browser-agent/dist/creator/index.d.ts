import './steps/index.js';
import type { Plan, PlanStep } from './types.js';
interface RecorderStatus {
    active: boolean;
    planName: string;
    recordedSteps: number;
}
interface FbAgentApi {
    run(plan: Plan, variables: Record<string, unknown>): Promise<{
        ok: boolean;
        error?: string;
    }>;
    startRecording(planName: string): Promise<void>;
    stopRecording(): Promise<{
        planName: string;
        steps: PlanStep[];
    }>;
    getRecorderStatus(): RecorderStatus;
    version: string;
}
declare const api: FbAgentApi;
export { api };
//# sourceMappingURL=index.d.ts.map