import { describe, it } from 'node:test';
import assert from 'node:assert';
import { CreateAdsetStep } from './create_adset.js';

// Идемпотентность по имени адсета.
describe('CreateAdsetStep', () => {
  it('isSatisfied при совпадении имени', () => {
    const s = new CreateAdsetStep();
    assert.equal(
      s.isSatisfied({ kind: 'matched', current: { name: 'AS1' } }, { name: 'AS1' }),
      true,
    );
    assert.equal(
      s.isSatisfied({ kind: 'matched', current: { name: 'X' } }, { name: 'AS1' }),
      false,
    );
  });
});
