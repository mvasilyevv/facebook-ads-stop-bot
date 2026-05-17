import type { Plan } from './types.js';
interface FbAgentApi {
    run(plan: Plan, variables: Record<string, unknown>): Promise<{
        ok: boolean;
        error?: string;
    }>;
    startRecording(planName: string): Promise<void>;
    stopRecording(): Promise<void>;
    version: string;
}
declare const api: FbAgentApi;
export { api };
//# sourceMappingURL=index.d.ts.map