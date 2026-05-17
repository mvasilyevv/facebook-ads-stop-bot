import type { Plan } from './types.js';

interface FbAgentApi {
  run(plan: Plan, variables: Record<string, unknown>): Promise<{ ok: boolean; error?: string }>;
  startRecording(planName: string): Promise<void>;
  stopRecording(): Promise<void>;
  version: string;
}

const VERSION = '2.0.0-phase1';

const api: FbAgentApi = {
  version: VERSION,
  async run(_plan, _variables) {
    return { ok: false, error: 'executor not implemented in phase1' };
  },
  async startRecording(_planName) {
    throw new Error('recorder not implemented in phase1');
  },
  async stopRecording() {
    throw new Error('recorder not implemented in phase1');
  },
};

(globalThis as any).window = (globalThis as any).window ?? {};
(globalThis as any).window.__fbAgent = api;

export { api };
