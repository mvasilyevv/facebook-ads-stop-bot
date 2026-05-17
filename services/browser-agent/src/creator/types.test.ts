import { describe, it } from 'node:test';
import assert from 'node:assert';
import type { StepState, RecordedEvent, PlanContext } from './types.js';

describe('creator types', () => {
  it('compiles without errors', () => {
    const _state: StepState = { kind: 'unknown' };
    const _ev: RecordedEvent = { type: 'click', selector: '.x', text: '', value: null };
    const _ctx: PlanContext = { variables: {}, emit: () => {} };
    assert.ok(true);
  });
});
