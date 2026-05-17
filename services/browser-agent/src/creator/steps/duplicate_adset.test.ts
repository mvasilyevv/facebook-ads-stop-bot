import { describe, it } from 'node:test';
import assert from 'node:assert';
import { DuplicateAdsetStep } from './duplicate_adset.js';

// Идемпотентность: уже есть newName в списке адсетов.
describe('DuplicateAdsetStep', () => {
  it('isSatisfied когда newName уже есть в дереве', () => {
    const s = new DuplicateAdsetStep();
    assert.equal(
      s.isSatisfied(
        { kind: 'matched', current: ['AS1', 'AS2'] },
        { sourceName: 'AS1', newName: 'AS2' },
      ),
      true,
    );
    assert.equal(
      s.isSatisfied(
        { kind: 'matched', current: ['AS1'] },
        { sourceName: 'AS1', newName: 'AS2' },
      ),
      false,
    );
  });
});
