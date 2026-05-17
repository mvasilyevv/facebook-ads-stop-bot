import { describe, it } from 'node:test';
import assert from 'node:assert';
import { RenameAdsetStep } from './rename_adset.js';

// Идемпотентность: to уже в списке, from удалён.
describe('RenameAdsetStep', () => {
  it('isSatisfied когда есть to и нет from', () => {
    const s = new RenameAdsetStep();
    assert.equal(
      s.isSatisfied(
        { kind: 'matched', current: ['AS1_v2', 'AS3'] },
        { from: 'AS1', to: 'AS1_v2' },
      ),
      true,
    );
    assert.equal(
      s.isSatisfied(
        { kind: 'matched', current: ['AS1', 'AS1_v2'] },
        { from: 'AS1', to: 'AS1_v2' },
      ),
      false,
    );
  });
});
