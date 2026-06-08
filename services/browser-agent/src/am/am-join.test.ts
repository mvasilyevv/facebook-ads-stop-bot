// H-8 (BA-3): money-критичный маппинг am_tabular → ScannedAdRow. Регресс на класс
// «shape прошёл, семантика сломалась»: метрики/конверсии напрямую кормят стоп-правила.

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';

import { buildScannedRow, buildScannedRows, mapEffectiveStatus } from './am-join.js';
import type { AmRow } from './am-parser.js';

function amRow(over: Partial<AmRow> = {}): AmRow {
  return {
    adId: '123',
    objective: null,
    atomic: {},
    actions: {},
    costPerAction: {},
    outboundClicks: null,
    outboundCtr: null,
    results: null,
    costPerResult: null,
    ...over,
  };
}

describe('buildScannedRow money-маппинг (H-8)', () => {
  it('полная строка: метрики + конверсии + meta', () => {
    const row = buildScannedRow(
      amRow({
        adId: '777',
        atomic: {
          spend: '12.34',
          impressions: '1000',
          reach: '900',
          clicks: '50',
          cpc: '0.24',
          ctr: '5.0',
          cpm: '12.3',
          frequency: '1.11',
        },
        actions: { lead: '7', omni_complete_registration: '3', landing_page_view: '20' },
        costPerAction: { lead: '1.76', omni_complete_registration: '4.11', landing_page_view: '0.61' },
        outboundClicks: '40',
        outboundCtr: '4.0',
        results: '2',
        costPerResult: '6.17',
      }),
      {
        adName: 'Ad X',
        campaignName: 'CR2 | KE | MV',
        campaignId: 'c1',
        adsetName: 'as1',
        effectiveStatus: 'ACTIVE',
      },
    );

    assert.equal(row.fb_ad_id, '777');
    assert.equal(row.spend, '12.34');
    assert.equal(row.impressions, 1000);
    assert.equal(row.reach, 900);
    assert.equal(row.clicks, 50);
    assert.equal(row.cpc, '0.24');
    assert.equal(row.cpm, '12.3');
    assert.equal(row.frequency, '1.11');
    // Конверсии: лиды/реги/LPV из своих action_type, depo из results.
    assert.equal(row.leads, 7);
    assert.equal(row.cost_per_lead, '1.76');
    assert.equal(row.registrations, 3);
    assert.equal(row.cost_per_registration, '4.11');
    assert.equal(row.landing_page_views, 20);
    assert.equal(row.cost_per_landing_page_view, '0.61');
    assert.equal(row.deposits, 2);
    assert.equal(row.cost_per_result, '6.17');
    assert.equal(row.outbound_clicks, 40);
    // Meta.
    assert.equal(row.delivery_status, 'ACTIVE');
    assert.equal(row.campaign_name, 'CR2 | KE | MV');
    assert.equal(row.ad_name, 'Ad X');
  });

  it('пустая строка: spend дефолтит "0", счётчики 0, опц. Decimal → null', () => {
    const row = buildScannedRow(amRow({ adId: 'e' }));
    assert.equal(row.spend, '0'); // money всегда заполнен (как в DOM-пути)
    assert.equal(row.impressions, 0);
    assert.equal(row.leads, 0);
    assert.equal(row.registrations, 0);
    assert.equal(row.deposits, 0);
    assert.equal(row.cpc, null); // отсутствует → null, не "0"
    assert.equal(row.cost_per_lead, null);
    assert.equal(row.delivery_status, 'UNKNOWN'); // нет статуса
    assert.equal(row.campaign_name, ''); // нет meta
    assert.equal(row.ad_name, '');
  });

  it('мелкие десятичные НЕ ломаются locale-эвристикой ("0.005" остаётся)', () => {
    const row = buildScannedRow(amRow({ atomic: { cpc: '0.005', spend: '0.01', ctr: '0.50' } }));
    assert.equal(row.cpc, '0.005');
    assert.equal(row.spend, '0.01');
    assert.equal(row.ctr, '0.50');
  });

  it('amInt: нечисло → 0, дробное → trunc', () => {
    assert.equal(buildScannedRow(amRow({ atomic: { impressions: 'abc' } })).impressions, 0);
    assert.equal(buildScannedRow(amRow({ atomic: { impressions: '1000.9' } })).impressions, 1000);
  });
});

describe('mapEffectiveStatus (H-8)', () => {
  it('известные статусы → канон', () => {
    assert.equal(mapEffectiveStatus('ACTIVE'), 'ACTIVE');
    assert.equal(mapEffectiveStatus('PAUSED'), 'OFF');
    assert.equal(mapEffectiveStatus('ADSET_PAUSED'), 'OFF');
    assert.equal(mapEffectiveStatus('ARCHIVED'), 'OFF');
    assert.equal(mapEffectiveStatus('PENDING_REVIEW'), 'IN_REVIEW');
    assert.equal(mapEffectiveStatus('DISAPPROVED'), 'NOT_DELIVERING');
    assert.equal(mapEffectiveStatus('IN_PROCESS'), 'PROCESSING');
  });

  it('пусто → UNKNOWN, неизвестное → passthrough (uppercase)', () => {
    assert.equal(mapEffectiveStatus(''), 'UNKNOWN');
    assert.equal(mapEffectiveStatus(undefined), 'UNKNOWN');
    assert.equal(mapEffectiveStatus('some_new_status'), 'SOME_NEW_STATUS');
  });
});

describe('buildScannedRows (H-8)', () => {
  it('итерирует merged-карту + резолвит meta по adId', () => {
    const merged = new Map<string, AmRow>([
      ['a1', amRow({ adId: 'a1', atomic: { spend: '1' } })],
      ['a2', amRow({ adId: 'a2', atomic: { spend: '2' } })],
    ]);
    const adMeta = new Map([['a1', { adName: 'A1', effectiveStatus: 'ACTIVE' }]]);
    const rows = buildScannedRows(merged, adMeta);
    assert.equal(rows.length, 2);
    const r1 = rows.find((r) => r.fb_ad_id === 'a1');
    const r2 = rows.find((r) => r.fb_ad_id === 'a2');
    assert.equal(r1?.ad_name, 'A1');
    assert.equal(r1?.delivery_status, 'ACTIVE');
    assert.equal(r2?.ad_name, ''); // нет meta → дефолты
    assert.equal(r2?.delivery_status, 'UNKNOWN');
  });
});
