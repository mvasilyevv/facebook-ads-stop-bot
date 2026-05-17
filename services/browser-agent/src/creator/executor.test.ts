import { describe, it, beforeEach } from 'node:test';
import assert from 'node:assert';
import { runPlan, interpolate } from './executor.js';
import { registerStep, clearRegistry } from './registry.js';
import { BaseStep } from './steps/base.js';
import type { StepState } from './types.js';

class Capture extends BaseStep<{ v: string }, void> {
  name = 'cap';
  received: string | null = null;
  detect(): StepState {
    return { kind: 'unknown' };
  }
  isSatisfied(): boolean {
    return false;
  }
  protected async run(_s: StepState, i: { v: string }): Promise<void> {
    this.received = i.v;
  }
}

describe('executor', () => {
  beforeEach(() => clearRegistry());

  it('interpolate подставляет {{geo}}', () => {
    const out = interpolate(
      { v: '{{geo}}-{{offer.code}}' },
      { geo: 'DE', offer: { code: 'CR2' } },
    );
    assert.deepEqual(out, { v: 'DE-CR2' });
  });

  it('runPlan выполняет шаги по очереди и эмитит events', async () => {
    const step = new Capture();
    registerStep(step);
    const events: Array<[string, unknown]> = [];
    const result = await runPlan(
      { schema_version: 1, steps: [{ step: 'cap', input: { v: '{{geo}}' } }] },
      { geo: 'DE' },
      (e, p) => events.push([e, p]),
    );
    assert.equal(result.ok, true);
    assert.equal(step.received, 'DE');
    const types = events.map(([e]) => e);
    assert.ok(types.includes('step_started'));
    assert.ok(types.includes('step_finished'));
  });

  it('runPlan возвращает {ok:false} при неизвестном шаге', async () => {
    const result = await runPlan(
      { schema_version: 1, steps: [{ step: 'nope', input: {} }] },
      {},
      () => {},
    );
    assert.equal(result.ok, false);
    assert.match(result.error || '', /unknown step/i);
  });
});
