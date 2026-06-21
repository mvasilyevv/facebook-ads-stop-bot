// adsManagerColumnsQs: дефолт содержит набор колонок/пресет; env переопределяет.
import { describe, it, afterEach } from 'node:test';
import assert from 'node:assert/strict';

import { adsManagerColumnsQs } from './am-columns-preset.js';

describe('adsManagerColumnsQs', () => {
  afterEach(() => {
    delete process.env.BROWSER_AGENT_AM_COLUMNS_QS;
  });

  // Дефолтный пресет — колонки пользователя + column_preset + attribution_windows.
  it('дефолт содержит columns, column_preset и attribution_windows', () => {
    delete process.env.BROWSER_AGENT_AM_COLUMNS_QS;
    const qs = adsManagerColumnsQs();
    assert.ok(qs.includes('columns='), 'есть columns');
    assert.ok(qs.includes('column_preset=1030561339462971'), 'есть column_preset');
    assert.ok(qs.includes('attribution_windows=default'), 'есть attribution_windows');
  });

  // Env-переменная переопределяет дефолт (смена набора колонок без пересборки).
  it('env BROWSER_AGENT_AM_COLUMNS_QS переопределяет дефолт', () => {
    process.env.BROWSER_AGENT_AM_COLUMNS_QS = 'columns=name&column_preset=999';
    assert.equal(adsManagerColumnsQs(), 'columns=name&column_preset=999');
  });

  // Пустая env-переменная игнорируется (фолбэк на дефолт).
  it('пустая env игнорируется → дефолт', () => {
    process.env.BROWSER_AGENT_AM_COLUMNS_QS = '   ';
    assert.ok(adsManagerColumnsQs().includes('column_preset=1030561339462971'));
  });
});
