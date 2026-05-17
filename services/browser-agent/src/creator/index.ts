import { runPlan } from './executor.js';
import type { Plan } from './types.js';

interface FbAgentApi {
  run(
    plan: Plan,
    variables: Record<string, unknown>,
  ): Promise<{ ok: boolean; error?: string }>;
  startRecording(planName: string): Promise<void>;
  stopRecording(): Promise<void>;
  version: string;
}

const VERSION = '2.0.0';

const api: FbAgentApi = {
  version: VERSION,
  async run(plan, variables) {
    const emit = (event: string, payload?: unknown) => {
      const fn = (globalThis as any).fbAgentEmit;
      if (typeof fn === 'function') fn(event, payload);
    };
    return runPlan(plan, variables, emit);
  },
  async startRecording(_planName) {
    throw new Error('recorder wired in phase 4');
  },
  async stopRecording() {
    throw new Error('recorder wired in phase 4');
  },
};

(globalThis as any).window = (globalThis as any).window ?? {};
(globalThis as any).window.__fbAgent = api;

export { api };
