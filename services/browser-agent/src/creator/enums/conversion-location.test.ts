import { describe, it } from 'node:test';
import assert from 'node:assert';
import { ConversionLocation, conversionLocationLabels } from './conversion-location.js';

// Проверяем enum значений и наличие синонимов на двух языках.
describe('ConversionLocation', () => {
  it('перечисляет все ожидаемые значения', () => {
    assert.deepEqual(
      Object.values(ConversionLocation).sort(),
      ['APP', 'MESSENGER', 'WEBSITE', 'WEBSITE_AND_CALLS'],
    );
  });
  it('у каждого enum есть ru и en синонимы', () => {
    for (const k of Object.values(ConversionLocation)) {
      const labels = conversionLocationLabels[k];
      assert.ok(labels.ru.length > 0 && labels.en.length > 0, `нет синонимов для ${k}`);
    }
  });
});
