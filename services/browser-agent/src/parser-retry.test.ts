import assert from 'node:assert/strict';
import test from 'node:test';

import { detectLogicalDeliveryStatus, waitForParsedAdsRows } from './parser.js';
import type { ScannedAdRow } from './types.js';

function makeRow(overrides: Partial<ScannedAdRow> = {}): ScannedAdRow {
  return {
    fb_ad_id: '1234567890123',
    campaign_name: 'Кампания',
    adset_name: 'Группа',
    ad_name: 'Объявление',
    delivery_status: 'ACTIVE',
    spend: '0.10',
    budget: '100',
    reach: 1,
    impressions: 1,
    clicks: 1,
    cpc: '0.10',
    ctr: '1.0',
    outbound_clicks: 0,
    outbound_ctr: null,
    landing_page_views: 0,
    cost_per_landing_page_view: null,
    cost_per_result: null,
    cpm: null,
    frequency: null,
    leads: 0,
    cost_per_lead: null,
    registrations: 0,
    cost_per_registration: null,
    deposits: 0,
    resolved_offer_code: null,
    ...overrides,
  };
}

// Сценарий: если строки уже готовы, helper не должен делать лишние повторные чтения.
test('waitForParsedAdsRows сразу возвращает непустой результат', async () => {
  let attempts = 0;

  const rows = await waitForParsedAdsRows({} as never, {
    timeoutMs: 50,
    pollMs: 1,
    readRows: async () => {
      attempts += 1;
      return [makeRow()];
    },
  });

  assert.equal(rows.length, 1);
  assert.equal(attempts, 1);
});

// Сценарий: после краткого пустого состояния helper должен дождаться появления строк.
test('waitForParsedAdsRows повторяет чтение после временного нуля строк', async () => {
  let attempts = 0;

  const rows = await waitForParsedAdsRows({} as never, {
    timeoutMs: 100,
    pollMs: 1,
    readRows: async () => {
      attempts += 1;
      if (attempts < 3) {
        return [];
      }
      return [makeRow({ ad_name: 'DRC_CR2_CR010' })];
    },
  });

  assert.equal(rows.length, 1);
  assert.equal(rows[0]?.ad_name, 'DRC_CR2_CR010');
  assert.equal(attempts, 3);
});

// Сценарий: если строки так и не появились, helper должен вернуть пустой результат по таймауту.
test('waitForParsedAdsRows завершает ожидание пустым массивом по таймауту', async () => {
  let attempts = 0;

  const rows = await waitForParsedAdsRows({} as never, {
    timeoutMs: 10,
    pollMs: 1,
    readRows: async () => {
      attempts += 1;
      return [];
    },
  });

  assert.deepEqual(rows, []);
  assert.ok(attempts >= 1);
});

// Сценарий: выключенный тумблер имеет приоритет над текстом доставки и должен давать канонический OFF.
test('detectLogicalDeliveryStatus учитывает aria-checked тумблера', () => {
  assert.equal(detectLogicalDeliveryStatus('Показ кампании прекращен', 'false'), 'OFF');
  assert.equal(detectLogicalDeliveryStatus('Показ кампании прекращен', 'true'), 'NOT_DELIVERING');
});
