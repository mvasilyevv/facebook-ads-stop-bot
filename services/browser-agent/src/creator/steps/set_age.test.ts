import { describe, it } from 'node:test';
import assert from 'node:assert';
import { SetAgeStep } from './set_age.js';

// Идемпотентность диапазона возраста.
describe('SetAgeStep', () => {
  it('isSatisfied при совпадении диапазона', () => {
    const s = new SetAgeStep();
    assert.equal(
      s.isSatisfied(
        { kind: 'matched', current: { min: 18, max: 65 } },
        { min: 18, max: 65 },
      ),
      true,
    );
    assert.equal(
      s.isSatisfied(
        { kind: 'matched', current: { min: 18, max: 65 } },
        { min: 25, max: 45 },
      ),
      false,
    );
  });
});
