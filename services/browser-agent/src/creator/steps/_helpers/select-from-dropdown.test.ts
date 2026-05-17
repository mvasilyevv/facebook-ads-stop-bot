import { describe, it } from 'node:test';
import assert from 'node:assert';
import { resolveLabelToEnum } from './select-from-dropdown.js';

// Проверяем матчинг подписи к enum по нормализованным синонимам.
const labels = {
  WEBSITE: { ru: ['Сайт', 'Веб-сайт'], en: ['Website'] },
  APP: { ru: ['Приложение'], en: ['App'] },
};

describe('resolveLabelToEnum', () => {
  it('матчит ru синоним', () =>
    assert.equal(resolveLabelToEnum('  сайт ', labels), 'WEBSITE'));
  it('матчит en label', () => assert.equal(resolveLabelToEnum('App', labels), 'APP'));
  it('возвращает null при отсутствии', () =>
    assert.equal(resolveLabelToEnum('xxx', labels), null));
});
