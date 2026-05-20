import './steps/index.js';
import { runPlan } from './executor.js';
import {
  startRecording as recStart,
  stopRecording as recStop,
  getStatus as recStatus,
} from './recorder.js';
import type { Plan, PlanStep } from './types.js';

interface RecorderStatus {
  active: boolean;
  planName: string;
  recordedSteps: number;
}

interface FbAgentApi {
  run(
    plan: Plan,
    variables: Record<string, unknown>,
  ): Promise<{ ok: boolean; error?: string }>;
  startRecording(planName: string): Promise<void>;
  stopRecording(): Promise<{ planName: string; steps: PlanStep[] }>;
  getRecorderStatus(): RecorderStatus;
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
  async startRecording(planName) {
    recStart(planName);
  },
  async stopRecording() {
    return recStop();
  },
  getRecorderStatus() {
    return recStatus();
  },
};

(globalThis as any).window = (globalThis as any).window ?? {};
(globalThis as any).window.__fbAgent = api;

export { api };
