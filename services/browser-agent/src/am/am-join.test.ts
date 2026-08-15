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
        campaignId: '101',
        adsetId: '201',
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
    assert.equal(row.campaign_id, '101');
    assert.equal(row.adset_id, '201');
    assert.equal(row.ad_name, 'Ad X');
  });

  it('пустая строка получает transport placeholders, но помечается incomplete', () => {
    const row = buildScannedRow(amRow({ adId: 'e' }));
    assert.equal(row.spend, '0');
    assert.equal(row.impressions, 0);
    assert.equal(row.leads, 0);
    assert.equal(row.registrations, 0);
    assert.equal(row.deposits, 0);
    assert.equal(row.cpc, null); // отсутствует → null, не "0"
    assert.equal(row.cost_per_lead, null);
    assert.equal(row.delivery_status, 'UNKNOWN'); // нет статуса
    assert.equal(row.campaign_name, ''); // нет meta
    assert.equal(row.ad_name, '');
    assert.deepEqual(row.metric_issues, [
      'spend:missing',
      'reach:missing',
      'impressions:missing',
      'clicks:missing',
    ]);
  });

  it('мелкие десятичные НЕ ломаются locale-эвристикой ("0.005" остаётся)', () => {
    const row = buildScannedRow(amRow({ atomic: { cpc: '0.005', spend: '0.01', ctr: '0.50' } }));
    assert.equal(row.cpc, '0.005');
    assert.equal(row.spend, '0.01');
    assert.equal(row.ctr, '0.50');
  });

  it('нечисловой или дробный count не становится подтверждённым целым', () => {
    const baseAtomic = { spend: '1', reach: '10', clicks: '2' };
    const malformed = buildScannedRow(
      amRow({ atomic: { ...baseAtomic, impressions: 'abc' } }),
    );
    const fractional = buildScannedRow(
      amRow({ atomic: { ...baseAtomic, impressions: '1000.9' } }),
    );

    assert.equal(malformed.impressions, 0);
    assert.deepEqual(malformed.metric_issues, ['impressions:invalid']);
    assert.equal(fractional.impressions, 0);
    assert.deepEqual(fractional.metric_issues, ['impressions:invalid']);
  });
});

describe('LPV omni/non-omni (BA-6)', () => {
  it('omni_landing_page_view предпочитается (count+cost из omni)', () => {
    const row = buildScannedRow(
      amRow({
        actions: { omni_landing_page_view: '30', landing_page_view: '12' },
        costPerAction: { omni_landing_page_view: '0.40', landing_page_view: '1.00' },
      }),
    );
    // omni выигрывает, без суммирования (не 42)
    assert.equal(row.landing_page_views, 30);
    assert.equal(row.cost_per_landing_page_view, '0.40');
  });

  it('fallback на non-omni landing_page_view, если omni нет', () => {
    const row = buildScannedRow(
      amRow({
        actions: { landing_page_view: '12' },
        costPerAction: { landing_page_view: '1.00' },
      }),
    );
    assert.equal(row.landing_page_views, 12);
    assert.equal(row.cost_per_landing_page_view, '1.00');
  });

  it('нет ни omni, ни non-omni → 0/null', () => {
    const row = buildScannedRow(amRow({ actions: {}, costPerAction: {} }));
    assert.equal(row.landing_page_views, 0);
    assert.equal(row.cost_per_landing_page_view, null);
  });
});

describe('mapEffectiveStatus (H-8)', () => {
  it('известные статусы → канон', () => {
    assert.equal(mapEffectiveStatus('ACTIVE'), 'ACTIVE');
    assert.equal(mapEffectiveStatus('PAUSED'), 'PAUSED');
    assert.equal(mapEffectiveStatus('ADSET_PAUSED'), 'ADSET_PAUSED');
    assert.equal(mapEffectiveStatus('ARCHIVED'), 'ARCHIVED');
    assert.equal(mapEffectiveStatus('PENDING_REVIEW'), 'PENDING_REVIEW');
    assert.equal(mapEffectiveStatus('DISAPPROVED'), 'DISAPPROVED');
    assert.equal(mapEffectiveStatus('IN_PROCESS'), 'IN_PROCESS');
  });

  it('пусто → UNKNOWN, неизвестное → passthrough (uppercase)', () => {
    assert.equal(mapEffectiveStatus(''), 'UNKNOWN');
    assert.equal(mapEffectiveStatus(undefined), 'UNKNOWN');
    assert.equal(mapEffectiveStatus('some_new_status'), 'SOME_NEW_STATUS');
  });
});

describe('7 новых полей ScannedAdRow (крео + адсет)', () => {
  it('все 7 полей заполняются из AmAdMeta', () => {
    // Сценарий: все новые поля присутствуют — прокидываются в ScannedAdRow без изменений.
    const row = buildScannedRow(
      amRow({ adId: 'AD99' }),
      {
        adName: 'Test Ad',
        creativeThumbUrl: 'https://cdn.fb.com/thumb.jpg',
        creativeImageUrl: 'https://cdn.fb.com/full.jpg',
        pixelId: '123456789',
        dailyBudget: '200000',
        lifetimeBudget: '0',
        budgetRemaining: '150000',
        learningStage: 'LEARNING',
      },
    );
    assert.equal(row.creative_thumb_url, 'https://cdn.fb.com/thumb.jpg');
    assert.equal(row.creative_image_url, 'https://cdn.fb.com/full.jpg');
    assert.equal(row.adset_pixel_id, '123456789');
    assert.equal(row.adset_daily_budget, '200000');
    assert.equal(row.adset_lifetime_budget, '0');
    assert.equal(row.adset_budget_remaining, '150000');
    assert.equal(row.adset_learning_stage, 'LEARNING');
  });

  it('видео-крео: creative_image_url пустой (только thumbnail)', () => {
    // Сценарий: video-крео не имеет image_url → поле должно быть пустой строкой.
    const row = buildScannedRow(
      amRow({ adId: 'AD_VIDEO' }),
      { creativeThumbUrl: 'https://cdn.fb.com/video_thumb.jpg' },
    );
    assert.equal(row.creative_thumb_url, 'https://cdn.fb.com/video_thumb.jpg');
    assert.equal(row.creative_image_url, ''); // image_url не задан → дефолт ''
  });

  it('все 7 полей = пустые строки при отсутствии meta', () => {
    // Сценарий: meta пустой (archived объявление, нет данных адсета) — нет паники, все '' .
    const row = buildScannedRow(amRow({ adId: 'AD_ARCHIVED' }), {});
    assert.equal(row.creative_thumb_url, '');
    assert.equal(row.creative_image_url, '');
    assert.equal(row.adset_pixel_id, '');
    assert.equal(row.adset_daily_budget, '');
    assert.equal(row.adset_lifetime_budget, '');
    assert.equal(row.adset_budget_remaining, '');
    assert.equal(row.adset_learning_stage, '');
  });

  it('LEARNING_LIMITED прокидывается как есть', () => {
    // Сценарий: адсет в стадии LEARNING_LIMITED — строка передаётся без изменений.
    const row = buildScannedRow(amRow(), { learningStage: 'LEARNING_LIMITED' });
    assert.equal(row.adset_learning_stage, 'LEARNING_LIMITED');
  });

  it('lifetime_budget адсета 0 → "0", не пустая строка', () => {
    // Сценарий: lifetimeBudget="0" (явный ноль от Meta) должен сохраняться как "0", не дефолт ''.
    const row = buildScannedRow(amRow(), { lifetimeBudget: '0', dailyBudget: '100000' });
    assert.equal(row.adset_lifetime_budget, '0');
    assert.equal(row.adset_daily_budget, '100000');
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
