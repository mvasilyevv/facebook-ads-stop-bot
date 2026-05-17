import { describe, it } from 'node:test';
import assert from 'node:assert';
import { SetTrackingUrlStep } from './set_tracking_url.js';

// Идемпотентность по URL.
describe('SetTrackingUrlStep', () => {
  it('isSatisfied при равных URL', () => {
    const s = new SetTrackingUrlStep();
    const url = 'https://t.co?p={{adset.id}}';
    assert.equal(
      s.isSatisfied({ kind: 'matched', current: url }, { url }),
      true,
    );
    assert.equal(
      s.isSatisfied({ kind: 'matched', current: 'x' }, { url }),
      false,
    );
  });
});
