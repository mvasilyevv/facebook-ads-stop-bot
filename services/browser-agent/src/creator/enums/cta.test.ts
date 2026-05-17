import { describe, it } from 'node:test';
import assert from 'node:assert';
import { CallToAction, ctaLabels } from './cta.js';

// Проверяем enum CallToAction и наличие ru/en синонимов.
describe('CallToAction', () => {
  it('у каждого enum есть ru и en синонимы', () => {
    for (const k of Object.values(CallToAction)) {
      const labels = ctaLabels[k];
      assert.ok(labels.ru.length > 0 && labels.en.length > 0, `нет синонимов для ${k}`);
    }
  });
});
