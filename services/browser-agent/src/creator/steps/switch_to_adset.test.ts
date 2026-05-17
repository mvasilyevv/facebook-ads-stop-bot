import { describe, it } from 'node:test';
import assert from 'node:assert';
import { SwitchToAdsetStep } from './switch_to_adset.js';

// Идемпотентность: уже выбран нужный ad set.
describe('SwitchToAdsetStep', () => {
  it('isSatisfied при совпадении текущего', () => {
    const s = new SwitchToAdsetStep();
    assert.equal(
      s.isSatisfied({ kind: 'matched', current: 'AS1' }, { name: 'AS1' }),
      true,
    );
    assert.equal(
      s.isSatisfied({ kind: 'matched', current: 'AS2' }, { name: 'AS1' }),
      false,
    );
  });
});
