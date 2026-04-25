import assert from 'node:assert/strict';
import test from 'node:test';

import {
  buildAdsTableColumnWidthTargets,
  buildParserColumnLayout,
  collectMissingValidationColumns,
  normalizeVisibleHeaders,
  type HeaderSnapshot,
} from './ads-columns.js';

function header(surfaceKey: string, text: string, left: number): HeaderSnapshot {
  return { surfaceKey, text, left };
}

function buildFullHeaderSet(): HeaderSnapshot[] {
  return [
    header('toggle', 'Выкл./вкл.', 0),
    header('name', 'Название объявления', 40),
    header('delivery', 'Статус показа', 120),
    header('budget', 'Бюджет', 200),
    header('results', 'Результат', 280),
    header('reach', 'Охват', 360),
    header('impressions', 'Показы', 440),
    header('cost_per_result', 'Цена за результат', 520),
    header('spend', 'Сумма затрат', 600),
    header('clicks', 'Клики', 680),
    header('cpc', 'CPC', 760),
    header('actions', 'Лиды', 840),
    header('cost_per_action_type', 'Цена за лид', 920),
    header('actions', 'Завершенные регистрации', 1000),
    header('cost_per_action_type', 'Цена за завершенную регистрацию', 1080),
    header('ctr', 'CTR', 1160),
    header('campaign_group_name', 'Название кампании', 1240),
    header('campaign_name', 'Название группы объявлений', 1320),
    header('outbound_clicks', 'Исходящие клики', 1400),
    header('outbound_clicks_ctr', 'CTR исходящих кликов', 1480),
    header('actions', 'Просмотры целевой страницы', 1560),
    header('cost_per_action_type', 'Цена за просмотр целевой страницы', 1640),
    header('cpm', 'CPM', 1720),
    header('frequency', 'Частота', 1800),
  ];
}

// Сценарий: парсер должен строить layout по фактическому порядку заголовков, а не по захардкоженным индексам.
test('buildParserColumnLayout учитывает реальный порядок заголовков', () => {
  const { layout, missingColumns } = buildParserColumnLayout(buildFullHeaderSet());
  const orderedFields = layout.map((column) => column.fieldName);

  assert.deepEqual(missingColumns, []);
  assert.ok(orderedFields.indexOf('deposits') < orderedFields.indexOf('cost_per_result'));
  assert.ok(orderedFields.indexOf('leads') < orderedFields.indexOf('registrations'));
  assert.ok(orderedFields.indexOf('cost_per_lead') < orderedFields.indexOf('cost_per_registration'));
});

// Сценарий: если обязательную колонку переименовали, валидация должна явно пометить её как отсутствующую.
test('collectMissingValidationColumns находит переименованную обязательную колонку', () => {
  const headers = buildFullHeaderSet().map((item) => (
    item.surfaceKey === 'actions' && item.text === 'Завершенные регистрации'
      ? header(item.surfaceKey, 'Конверсии', item.left)
      : item
  ));

  const missingColumns = collectMissingValidationColumns(headers);

  assert.ok(missingColumns.includes('Завершенные регистрации'));
  assert.ok(!missingColumns.includes('Лиды'));
});

// Сценарий: диагностические и ранние traffic-колонки можно скрыть без поломки обязательного парсинга.
test('buildParserColumnLayout не требует необязательные traffic-колонки', () => {
  const headers = buildFullHeaderSet().filter((item) => ![
    'outbound_clicks',
    'outbound_clicks_ctr',
    'cpm',
    'frequency',
  ].includes(item.surfaceKey));

  const { layout, missingColumns } = buildParserColumnLayout(headers);
  const fields = layout.map((column) => column.fieldName);

  assert.deepEqual(missingColumns, []);
  assert.ok(fields.includes('deposits'));
  assert.ok(!fields.includes('outbound_clicks'));
});

// Сценарий: колонка результата обязательна, потому что в текущем Ads Manager она означает депозиты.
test('collectMissingValidationColumns требует колонку результата для депозитов', () => {
  const headers = buildFullHeaderSet().filter((item) => item.surfaceKey !== 'results');
  const missingColumns = collectMissingValidationColumns(headers);

  assert.ok(missingColumns.includes('Результат'));
});

// Сценарий: дубли header-нод не должны ломать подсчёт заголовков и смещение ячеек.
test('normalizeVisibleHeaders удаляет дублирующиеся заголовки', () => {
  const headers = [
    ...buildFullHeaderSet(),
    header('spend', 'Сумма затрат', 600.4),
    header('spend', 'Сумма затрат', 601.1),
  ];

  const normalized = normalizeVisibleHeaders(headers);
  const spendHeaders = normalized.filter((item) => item.surfaceKey === 'spend');

  assert.equal(spendHeaders.length, 1);
});

// Сценарий: нулевая геометрия hidden-header нод не должна ломать распознавание схемы колонок.
test('buildParserColumnLayout работает для заголовков без видимой геометрии', () => {
  const headers = buildFullHeaderSet().map((item) => ({ ...item, left: 0 }));
  const { layout, missingColumns } = buildParserColumnLayout(headers);

  assert.deepEqual(missingColumns, []);
  assert.ok(layout.some((column) => column.fieldName === 'ad_name'));
  assert.ok(layout.some((column) => column.fieldName === 'registrations'));
});

// Сценарий: служебные пустые header-ноды Meta не должны сдвигать индексы реальных колонок.
test('normalizeVisibleHeaders отбрасывает пустой дубль surfaceKey при наличии текстового заголовка', () => {
  const headers = [
    header('results', 'Результат', 0),
    header('results', '', 0),
    header('reach', 'Охват', 0),
  ];

  const normalized = normalizeVisibleHeaders(headers);

  assert.deepEqual(
    normalized.map((item) => ({ surfaceKey: item.surfaceKey, text: item.text })),
    [
      { surfaceKey: 'results', text: 'результат' },
      { surfaceKey: 'reach', text: 'охват' },
    ],
  );
});

// Сценарий: пресет автоширины должен повторять текущую ручную раскладку Ads Manager один в один.
test('buildAdsTableColumnWidthTargets возвращает сохранённые ширины Ads Manager', () => {
  const widths = Object.fromEntries(
    buildAdsTableColumnWidthTargets().map((target) => [target.key, target.widthPx]),
  );

  assert.equal(widths.toggle, 40);
  assert.equal(widths.name, 194);
  assert.equal(widths.deposits, 137);
  assert.equal(widths.cost_per_registration, 100);
  assert.equal(widths.frequency, 40);
});
