// Юнит-тесты детектора причины пустого скана.
// Сценарии: таблицы нет, таблица есть но фильтр исключает всё, таблица есть но активных кампаний нет.

import assert from 'node:assert/strict';
import test from 'node:test';

import { detectEmptyReason } from './empty-reason.js';

test('detectEmptyReason: возвращает table_not_found, когда нет хедера таблицы', () => {
  assert.equal(
    detectEmptyReason({ hasTableHeader: false, hasFilterChips: false, rowCount: 0 }),
    'table_not_found',
  );
});

test('detectEmptyReason: возвращает filter_excludes_all, когда хедер есть, есть фильтр-чипы и 0 строк', () => {
  assert.equal(
    detectEmptyReason({ hasTableHeader: true, hasFilterChips: true, rowCount: 0 }),
    'filter_excludes_all',
  );
});

test('detectEmptyReason: возвращает no_active_ads, когда хедер есть, фильтров нет и 0 строк', () => {
  assert.equal(
    detectEmptyReason({ hasTableHeader: true, hasFilterChips: false, rowCount: 0 }),
    'no_active_ads',
  );
});

test('detectEmptyReason: возвращает null, когда есть хотя бы одна строка', () => {
  assert.equal(
    detectEmptyReason({ hasTableHeader: true, hasFilterChips: false, rowCount: 1 }),
    null,
  );
});
