// runPlan: последовательно выполняет шаги плана с подстановкой переменных
// шаблонами {{var}} / {{obj.path}}, эмитит step_started/finished/failed/skipped,
// между шагами добавляет гуманизированную паузу.
import { getStep } from './registry.js';
import { humanIdle, IdleRange } from './humanizer.js';
import type { Plan, PlanContext } from './types.js';

export type Emit = (event: string, payload?: unknown) => void;

const TEMPLATE_RE = /\{\{\s*([\w.]+)\s*\}\}/g;

function resolvePath(obj: Record<string, unknown>, path: string): unknown {
  return path.split('.').reduce<unknown>((acc, key) => {
    if (acc && typeof acc === 'object' && key in (acc as Record<string, unknown>)) {
      return (acc as Record<string, unknown>)[key];
    }
    return undefined;
  }, obj);
}

export function interpolate<T>(input: T, vars: Record<string, unknown>): T {
  if (typeof input === 'string') {
    return input.replace(TEMPLATE_RE, (_, p) => {
      const v = resolvePath(vars, p);
      return v == null ? '' : String(v);
    }) as unknown as T;
  }
  if (Array.isArray(input)) {
    return input.map((x) => interpolate(x, vars)) as unknown as T;
  }
  if (input && typeof input === 'object') {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(input as Record<string, unknown>)) {
      out[k] = interpolate(v, vars);
    }
    return out as unknown as T;
  }
  return input;
}

export async function runPlan(
  plan: Plan,
  variables: Record<string, unknown>,
  emit: Emit,
): Promise<{ ok: boolean; error?: string }> {
  const ctx: PlanContext = { variables, emit };
  for (const step of plan.steps) {
    const impl = getStep(step.step);
    if (!impl) {
      emit('step_failed', { step: step.step, error: 'unknown step' });
      return { ok: false, error: `unknown step: ${step.step}` };
    }
    const input = interpolate(step.input, variables);
    emit('step_started', { step: step.step });
    try {
      const state = await impl.detect(ctx);
      await impl.execute(state, input, ctx);
      emit('step_finished', { step: step.step });
    } catch (e: any) {
      emit('step_failed', { step: step.step, error: String(e?.message ?? e) });
      return { ok: false, error: String(e?.message ?? e) };
    }
    await humanIdle(IdleRange.BETWEEN_STEPS);
  }
  return { ok: true };
}
