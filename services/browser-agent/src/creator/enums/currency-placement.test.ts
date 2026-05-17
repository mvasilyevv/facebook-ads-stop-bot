import { describe, it } from 'node:test';
import assert from 'node:assert';
import { Currency } from './currency.js';
import { Placement, placementLabels } from './placement.js';

describe('Currency', () => {
  it('содержит набор поддерживаемых валют', () => {
    assert.deepEqual(Object.values(Currency).sort(), ['EUR', 'RUB', 'UAH', 'USD']);
  });
});

describe('Placement', () => {
  it('у каждого enum есть ru и en синонимы', () => {
    for (const k of Object.values(Placement)) {
      const labels = placementLabels[k];
      assert.ok(labels.ru.length > 0 && labels.en.length > 0, `нет синонимов для ${k}`);
    }
  });
});
