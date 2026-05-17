import { describe, it } from 'node:test';
import assert from 'node:assert';
import { ReattachCreativeStep } from './reattach_creative.js';

// Идемпотентность — кол-во прикрепленных совпадает с paths.
describe('ReattachCreativeStep', () => {
  it('isSatisfied при равенстве кол-ва превью и paths', () => {
    const s = new ReattachCreativeStep();
    assert.equal(
      s.isSatisfied({ kind: 'matched', current: 1 }, { adName: 'A', paths: ['a.jpg'] }),
      true,
    );
    assert.equal(
      s.isSatisfied({ kind: 'matched', current: 0 }, { adName: 'A', paths: ['a.jpg'] }),
      false,
    );
  });
});
