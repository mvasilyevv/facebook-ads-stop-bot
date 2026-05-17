import { describe, it } from 'node:test';
import assert from 'node:assert';
import { RenameAdStep } from './rename_ad.js';

// Идемпотентность переименования объявления.
describe('RenameAdStep', () => {
  it('isSatisfied когда есть to и нет from', () => {
    const s = new RenameAdStep();
    assert.equal(
      s.isSatisfied(
        { kind: 'matched', current: ['Ad_v2'] },
        { from: 'Ad', to: 'Ad_v2' },
      ),
      true,
    );
  });
});
