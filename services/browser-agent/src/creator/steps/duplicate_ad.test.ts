import { describe, it } from 'node:test';
import assert from 'node:assert';
import { DuplicateAdStep } from './duplicate_ad.js';

// Идемпотентность: уже есть newName в списке объявлений.
describe('DuplicateAdStep', () => {
  it('isSatisfied когда newName уже есть в дереве', () => {
    const s = new DuplicateAdStep();
    assert.equal(
      s.isSatisfied(
        { kind: 'matched', current: ['Ad1', 'Ad2'] },
        { sourceName: 'Ad1', newName: 'Ad2' },
      ),
      true,
    );
  });
});
