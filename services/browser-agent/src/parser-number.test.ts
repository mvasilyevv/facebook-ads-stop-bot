import assert from 'node:assert/strict';
import test from 'node:test';

import {
  parseDecimalOrNull,
  parseIntValue,
  parseMoney,
  parseMoneyOrNull,
} from './parser.js';

// Сценарий: денежные значения с русскими и английскими разделителями тысяч не занижаются.
test('parseMoney корректно нормализует разделители тысяч и дробной части', () => {
  const cases = [
    ['1 234,56', '1234.56'],
    ['1,234.56', '1234.56'],
    ['$1,234.56', '1234.56'],
    ['€1.234,56', '1234.56'],
    ['1,234', '1234'],
    ['1 234', '1234'],
    ['1.234', '1234'],
    ['1234,56', '1234.56'],
    ['1 234 567,89', '1234567.89'],
  ];

  for (const [input, expected] of cases) {
    assert.equal(parseMoney(input), expected);
  }
});

// Сценарий: nullable money parser возвращает null для пустых значений и число для валютных строк.
test('parseMoneyOrNull различает пустые и валидные денежные значения', () => {
  assert.equal(parseMoneyOrNull('—'), null);
  assert.equal(parseMoneyOrNull('--'), null);
  assert.equal(parseMoneyOrNull('$1,234.56'), '1234.56');
});

// Сценарий: целочисленные метрики с разделителями тысяч читаются как тысячи, а не как единицы.
test('parseIntValue корректно читает целые метрики с разделителями тысяч', () => {
  assert.equal(parseIntValue('1,234'), 1234);
  assert.equal(parseIntValue('1 234'), 1234);
  assert.equal(parseIntValue('1.234'), 1234);
  assert.equal(parseIntValue('12,345'), 12345);
  assert.equal(parseIntValue('1 234 567'), 1234567);
});

// Сценарий: десятичные метрики сохраняют дробную часть для русской и английской локали.
test('parseDecimalOrNull сохраняет дробную часть десятичных метрик', () => {
  assert.equal(parseDecimalOrNull('12,34%'), '12.34');
  assert.equal(parseDecimalOrNull('12.34%'), '12.34');
  assert.equal(parseDecimalOrNull('1,234'), '1234');
});
