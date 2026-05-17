import { describe, it } from 'node:test';
import assert from 'node:assert';
import { ClickNextStep } from './click_next.js';

// click_next — переходный шаг, isSatisfied всегда false.
describe('ClickNextStep', () => {
  it('isSatisfied всегда false', () => {
    const s = new ClickNextStep();
    assert.equal(s.isSatisfied(), false);
  });
});
