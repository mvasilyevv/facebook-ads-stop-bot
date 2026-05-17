import { describe, it, beforeEach } from 'node:test';
import assert from 'node:assert';
import { registerStep, getStep, listSteps, clearRegistry } from './registry.js';
import type { Step } from './types.js';

const dummy: Step = {
  name: 'dummy',
  detect: () => ({ kind: 'unknown' }),
  isSatisfied: () => false,
  execute: async () => ({}),
};

describe('registry', () => {
  beforeEach(() => clearRegistry());

  it('регистрирует и возвращает шаг по имени', () => {
    registerStep(dummy);
    assert.strictEqual(getStep('dummy'), dummy);
  });

  it('listSteps возвращает все', () => {
    registerStep(dummy);
    assert.deepEqual(
      listSteps().map((s) => s.name),
      ['dummy'],
    );
  });

  it('падает при попытке зарегистрировать дубликат', () => {
    registerStep(dummy);
    assert.throws(() => registerStep(dummy), /already registered/);
  });
});
