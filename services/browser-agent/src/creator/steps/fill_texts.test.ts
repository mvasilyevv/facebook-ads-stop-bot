import { describe, it } from 'node:test';
import assert from 'node:assert';
import { FillTextsStep } from './fill_texts.js';

// Идемпотентность: true только когда все три поля совпали.
describe('FillTextsStep', () => {
  it('isSatisfied при совпадении всех полей', () => {
    const s = new FillTextsStep();
    assert.equal(
      s.isSatisfied(
        { kind: 'matched', current: { primary: 'P', headline: 'H', description: 'D' } },
        { primary: 'P', headline: 'H', description: 'D' },
      ),
      true,
    );
  });

  it('isSatisfied false при отличии headline', () => {
    const s = new FillTextsStep();
    assert.equal(
      s.isSatisfied(
        { kind: 'matched', current: { primary: 'P', headline: 'X', description: '' } },
        { primary: 'P', headline: 'H' },
      ),
      false,
    );
  });
});
