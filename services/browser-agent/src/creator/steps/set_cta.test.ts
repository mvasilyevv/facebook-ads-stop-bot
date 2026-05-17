import { describe, it } from 'node:test';
import assert from 'node:assert';
import { SetCtaStep } from './set_cta.js';
import { CallToAction } from '../enums/index.js';

// Идемпотентность CTA.
describe('SetCtaStep', () => {
  it('isSatisfied при совпадении CTA', () => {
    const s = new SetCtaStep();
    assert.equal(
      s.isSatisfied(
        { kind: 'matched', current: CallToAction.SHOP_NOW },
        { value: CallToAction.SHOP_NOW },
      ),
      true,
    );
    assert.equal(
      s.isSatisfied(
        { kind: 'matched', current: CallToAction.LEARN_MORE },
        { value: CallToAction.SHOP_NOW },
      ),
      false,
    );
  });
});
