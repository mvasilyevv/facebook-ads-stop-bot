import { describe, it } from 'node:test';
import assert from 'node:assert';
import { PixelEvent, pixelEventLabels } from './pixel-event.js';

// Проверяем enum PixelEvent и наличие ru/en синонимов.
describe('PixelEvent', () => {
  it('у каждого enum есть ru и en синонимы', () => {
    for (const k of Object.values(PixelEvent)) {
      const labels = pixelEventLabels[k];
      assert.ok(labels.ru.length > 0 && labels.en.length > 0, `нет синонимов для ${k}`);
    }
  });
});
