// Реестр шагов: имя → Step. Регистрация дубликата — ошибка.
import type { Step } from './types.js';

const _registry = new Map<string, Step>();

export function registerStep(step: Step): void {
  if (_registry.has(step.name)) {
    throw new Error(`Step ${step.name} already registered`);
  }
  _registry.set(step.name, step);
}

export function getStep(name: string): Step | undefined {
  return _registry.get(name);
}

export function listSteps(): Step[] {
  return Array.from(_registry.values());
}

export function clearRegistry(): void {
  _registry.clear();
}
