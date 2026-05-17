import { describe, it } from 'node:test';
import assert from 'node:assert';
import { SetConversionLocationStep } from './set_conversion_location.js';
import { ConversionLocation } from '../enums/index.js';

// Идемпотентность: true при совпадении, false при отличии.
describe('SetConversionLocationStep', () => {
  it('isSatisfied true когда current === input.value', () => {
    const s = new SetConversionLocationStep();
    assert.equal(
      s.isSatisfied(
        { kind: 'matched', current: ConversionLocation.WEBSITE },
        { value: ConversionLocation.WEBSITE },
      ),
      true,
    );
  });

  it('isSatisfied false при отличии', () => {
    const s = new SetConversionLocationStep();
    assert.equal(
      s.isSatisfied(
        { kind: 'matched', current: ConversionLocation.APP },
        { value: ConversionLocation.WEBSITE },
      ),
      false,
    );
  });
});
