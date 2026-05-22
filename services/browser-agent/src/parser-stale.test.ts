// Тесты helpers countEmptyMetricsRows и findPartialRows из parser.ts.

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';

import { countEmptyMetricsRows, findPartialRows } from './parser.js';
import type { ScannedAdRow } from './types.js';

function makeRow(overrides: Partial<ScannedAdRow>): ScannedAdRow {
  return {
    fb_ad_id: '1',
    campaign_name: 'c',
    adset_name: 'a',
    ad_name: 'n',
    delivery_status: 'Активно',
    spend: '',
    budget: '',
    reach: 0,
    impressions: 0,
    clicks: 0,
    cpc: null,
    ctr: null,
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

describe('countEmptyMetricsRows', () => {
  it('считает строку пустой, если все критические метрики = "" / "—" / null / 0', () => {
    const row = makeRow({ impressions: 0, spend: '', cpm: '—', cpc: '', ctr: '' });
    assert.equal(countEmptyMetricsRows([row]), 1);
  });

  it('считает строку пустой при null-значениях метрик', () => {
    const row = makeRow({ impressions: 0, spend: '', cpm: null, cpc: null, ctr: null });
    assert.equal(countEmptyMetricsRows([row]), 1);
  });

  it('не считает пустой, если хотя бы одна критическая метрика непустая', () => {
    const row = makeRow({ impressions: 100, spend: '' });
    assert.equal(countEmptyMetricsRows([row]), 0);
  });

  it('не считает пустой, если spend > 0', () => {
    const row = makeRow({ impressions: 0, spend: '5.50' });
    assert.equal(countEmptyMetricsRows([row]), 0);
  });
});

describe('findPartialRows', () => {
  it('возвращает fb_ad_id строк с пустыми ad_name или campaign_name', () => {
    const rows = [
      makeRow({ fb_ad_id: '1', ad_name: '', campaign_name: 'c' }),
      makeRow({ fb_ad_id: '2', ad_name: 'n', campaign_name: 'c' }),
      makeRow({ fb_ad_id: '3', ad_name: 'n', campaign_name: '' }),
    ];
    assert.deepEqual(findPartialRows(rows), ['1', '3']);
  });

  it('пропускает строки без fb_ad_id', () => {
    const rows = [makeRow({ fb_ad_id: '', ad_name: '', campaign_name: '' })];
    assert.deepEqual(findPartialRows(rows), []);
  });
});
