import { describe, it } from 'node:test';
import assert from 'node:assert';
import { SaveDraftStep } from './save_draft.js';

// Идемпотентность: если индикатор «Сохранено» уже виден — пропуск.
describe('SaveDraftStep', () => {
  it('isSatisfied при наличии индикатора saved', () => {
    const s = new SaveDraftStep();
    assert.equal(s.isSatisfied({ kind: 'matched', current: 'saved' }), true);
    assert.equal(s.isSatisfied({ kind: 'missing' }), false);
  });
});
