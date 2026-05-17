import { describe, it } from 'node:test';
import assert from 'node:assert';
import { BaseStep } from './base.js';
import type { PlanContext, StepState } from '../types.js';

class FakeStep extends BaseStep<{ value: string }, void> {
  name = 'fake';
  executed = false;
  detect(_ctx: PlanContext): StepState {
    return { kind: 'matched', current: 'A' };
  }
  isSatisfied(state: StepState, input: { value: string }): boolean {
    return state.current === input.value;
  }
  protected async run(): Promise<void> {
    this.executed = true;
  }
}

const ctx: PlanContext = { variables: {}, emit: () => {} };

describe('BaseStep', () => {
  it('skip когда уже satisfied', async () => {
    const s = new FakeStep();
    const state = s.detect(ctx);
    await s.execute(state as any, { value: 'A' }, ctx);
    assert.equal(s.executed, false);
  });

  it('исполняет когда не satisfied', async () => {
    const s = new FakeStep();
    const state = s.detect(ctx);
    await s.execute(state as any, { value: 'B' }, ctx);
    assert.equal(s.executed, true);
  });
});
