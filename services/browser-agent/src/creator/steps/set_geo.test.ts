import { describe, it } from 'node:test';
import assert from 'node:assert';
import { SetGeoStep } from './set_geo.js';

// Идемпотентность: true когда все требуемые страны уже выбраны.
describe('SetGeoStep', () => {
  it('isSatisfied когда current содержит все требуемые страны', () => {
    const s = new SetGeoStep();
    assert.equal(
      s.isSatisfied({ kind: 'matched', current: ['DE', 'AT'] }, { countries: ['DE'] }),
      true,
    );
    assert.equal(
      s.isSatisfied({ kind: 'matched', current: ['DE'] }, { countries: ['DE', 'AT'] }),
      false,
    );
  });
});
