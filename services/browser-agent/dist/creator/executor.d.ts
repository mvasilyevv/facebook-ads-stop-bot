import type { Plan } from './types.js';
export type Emit = (event: string, payload?: unknown) => void;
export declare function interpolate<T>(input: T, vars: Record<string, unknown>): T;
export declare function runPlan(plan: Plan, variables: Record<string, unknown>, emit: Emit): Promise<{
    ok: boolean;
    error?: string;
}>;
//# sourceMappingURL=executor.d.ts.map