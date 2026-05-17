import { describe, it } from 'node:test';
import assert from 'node:assert';
import { normalizeText } from './text.js';

describe('normalizeText', () => {
  it('нижний регистр + триминг + схлопывает пробелы', () => {
    assert.equal(normalizeText('  Сайт   и звонки  '), 'сайт и звонки');
  });
  it('удаляет невидимые символы', () => {
    assert.equal(normalizeText('Web​site'), 'website');
  });
  it('идемпотентен', () => {
    const a = normalizeText('Сайт');
    assert.equal(normalizeText(a), a);
  });
});
