import { describe, it } from 'node:test';
import assert from 'node:assert';
import { AttributionWindow, attributionLabels } from './attribution.js';

// Проверяем enum AttributionWindow и наличие ru/en синонимов.
describe('AttributionWindow', () => {
  it('у каждого enum есть ru и en синонимы', () => {
    for (const k of Object.values(AttributionWindow)) {
      const labels = attributionLabels[k];
      assert.ok(labels.ru.length > 0 && labels.en.length > 0, `нет синонимов для ${k}`);
    }
  });
});
