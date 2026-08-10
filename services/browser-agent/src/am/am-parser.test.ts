import assert from 'node:assert/strict';
import test from 'node:test';

import { parseAmTabular, mergeAmRows, parseLightList, lightNextCursor } from './am-parser.js';
import { buildScannedRow, buildScannedRows, mapEffectiveStatus } from './am-join.js';

// Заголовки per-ad ответа am_tabular — точная копия боевой структуры (level=ad).
function amHeaders() {
  return {
    dimensions: ['objective', 'ad_id', 'date_start', 'date_stop'],
    atomic_columns: [
      { name: 'anchor_event_attribution_setting', type: 'string' },
      { name: 'multi_event_conversion_attribution_setting', type: 'string' },
      { name: 'reach', type: 'numeric string' },
      { name: 'impressions', type: 'numeric string' },
      { name: 'spend', type: 'numeric string' },
      { name: 'clicks', type: 'numeric string' },
      { name: 'cpc', type: 'numeric string' },
      { name: 'ctr', type: 'numeric string' },
      { name: 'cpm', type: 'numeric string' },
      { name: 'frequency', type: 'numeric string' },
      { name: 'attribution_setting', type: 'string' },
      { name: 'conversion_count_setting', type: 'string' },
    ],
    action_columns: [
      { name: 'actions', attribution_window: 'default' },
      { name: 'actions', attribution_window: 'inline' },
      { name: 'cost_per_action_type', attribution_window: 'default' },
      { name: 'cost_per_action_type', attribution_window: 'inline' },
      { name: 'outbound_clicks', attribution_window: 'default' },
      { name: 'outbound_clicks', attribution_window: 'inline' },
      { name: 'outbound_clicks_ctr', attribution_window: 'default' },
      { name: 'outbound_clicks_ctr', attribution_window: 'inline' },
      { name: 'conversion_annotations', attribution_window: 'default' },
      { name: 'conversion_annotations', attribution_window: 'inline' },
    ],
    result_columns: [
      { name: 'anchor_events', type: 'results', attribution_window: 'default' },
      { name: 'anchor_events', type: 'results', attribution_window: 'inline' },
      { name: 'results', type: 'results', attribution_window: 'default' },
      { name: 'results', type: 'results', attribution_window: 'inline' },
      { name: 'cost_per_result', type: 'results', attribution_window: 'default' },
      { name: 'cost_per_result', type: 'results', attribution_window: 'inline' },
    ],
  };
}

const EMPTY_ACTION = { breakdown: 'action_type' };

// Строка с конверсиями и результатом (ad ...570044 из live: dep=1, lead=1, reg=1).
function rowWithConversions() {
  return {
    dimension_values: ['OUTCOME_SALES', '120244531696570044', '2026-05-30', '2026-05-30'],
    atomic_values: ['na', 'na', '8', '8', '0.01', '2', '0.005', '25', '1.25', '1', '1d_click', 'ALL'],
    action_values: [
      {
        types: ['landing_page_view', 'lead', 'omni_landing_page_view', 'omni_complete_registration'],
        values: ['1', '1', '1', '1'],
        breakdown: 'action_type',
      },
      EMPTY_ACTION,
      {
        types: ['lead', 'omni_complete_registration', 'landing_page_view'],
        values: ['0.01', '0.01', '0.01'],
        breakdown: 'action_type',
      },
      EMPTY_ACTION,
      { types: ['outbound_click'], values: ['2'], breakdown: 'action_type' },
      EMPTY_ACTION,
      { types: ['outbound_click'], values: ['25'], breakdown: 'action_type' },
      EMPTY_ACTION,
      EMPTY_ACTION,
      EMPTY_ACTION,
    ],
    result_values: [
      { indicator: 'null' },
      { indicator: 'null' },
      { indicator: 'actions:offsite_conversion.fb_pixel_purchase', value: '1' },
      { indicator: 'actions:offsite_conversion.fb_pixel_purchase' },
      { indicator: 'actions:offsite_conversion.fb_pixel_purchase', value: '0.01' },
      { indicator: 'actions:offsite_conversion.fb_pixel_purchase' },
    ],
  };
}

// Пустая строка: метрики есть, конверсий/результата нет (action_values пустые, result_values null).
function rowEmpty() {
  return {
    dimension_values: ['OUTCOME_SALES', '120244530626090044', '2026-05-30', '2026-05-30'],
    atomic_values: ['na', 'na', '62', '63', '0.09', '0', 'null', '0', '1.428571', '1.016129', 'x', 'ALL'],
    action_values: [
      EMPTY_ACTION, EMPTY_ACTION, EMPTY_ACTION, EMPTY_ACTION, EMPTY_ACTION,
      EMPTY_ACTION, EMPTY_ACTION, EMPTY_ACTION, EMPTY_ACTION, EMPTY_ACTION,
    ],
    result_values: [
      { indicator: 'null' }, { indicator: 'null' }, { indicator: 'null' },
      { indicator: 'null' }, { indicator: 'null' }, { indicator: 'null' },
    ],
  };
}

// Summary-строка агрегата: ad_id="na" — должна отбрасываться.
function rowSummary() {
  return {
    dimension_values: ['OUTCOME_SALES', 'na', '2026-05-30', '2026-05-30'],
    atomic_values: ['multiple', '139', '999', '999', '99', '99', '0.1', '1.1', '14', '1.0', 'x', 'ALL'],
    action_values: [EMPTY_ACTION],
    result_values: [{ indicator: 'null' }],
  };
}

function amBody(rows: object[]) {
  return { data: [{ headers: amHeaders(), rows }] };
}

// parseAmTabular: summary-строка ad_id="na" отброшена, метрики/конверсии распарсены.
test('parseAmTabular: пропускает summary, парсит atomic+actions+result', () => {
  const rows = parseAmTabular(amBody([rowSummary(), rowWithConversions(), rowEmpty()]));
  assert.equal(rows.length, 2); // summary выкинут

  const a = rows[0];
  assert.equal(a.adId, '120244531696570044');
  assert.equal(a.atomic.spend, '0.01');
  assert.equal(a.atomic.impressions, '8');
  assert.equal(a.atomic.clicks, '2');
  assert.equal(a.actions.lead, '1');
  assert.equal(a.actions.omni_complete_registration, '1');
  assert.equal(a.actions.landing_page_view, '1');
  assert.equal(a.costPerAction.lead, '0.01');
  assert.equal(a.outboundClicks, '2');
  assert.equal(a.outboundCtr, '25');
  // deposits ← result_columns[name=results, default] = slot 2
  assert.equal(a.results, '1');
  assert.equal(a.costPerResult, '0.01');
});

// parseAmTabular: пустая строка — atomic есть, конверсий нет (null/na отфильтрованы).
test('parseAmTabular: пустые конверсии → пустые dict, results=null', () => {
  const rows = parseAmTabular(amBody([rowEmpty()]));
  const r = rows[0];
  assert.equal(r.atomic.spend, '0.09');
  assert.equal(r.atomic.clicks, '0');
  assert.equal(r.atomic.cpc, undefined); // "null" отфильтрован
  assert.deepEqual(r.actions, {});
  assert.equal(r.results, null);
  assert.equal(r.costPerResult, null);
});

// parseAmTabular: ответ без ad_id в dimensions (footer level=account) → пусто.
test('parseAmTabular: footer без ad_id игнорируется', () => {
  const footer = {
    data: [{ headers: { dimensions: ['objective', 'date_start', 'date_stop'] }, rows: [{}] }],
  };
  assert.deepEqual(parseAmTabular(footer), []);
  assert.deepEqual(parseAmTabular(null), []);
  assert.deepEqual(parseAmTabular({}), []);
});

// mergeAmRows: sync даёт result, async добивает actions — мёрж по ad_id берёт оба.
test('mergeAmRows: sync(result) + async(actions) → объединение', () => {
  const sync: ReturnType<typeof parseAmTabular> = parseAmTabular(
    amBody([
      {
        dimension_values: ['OUTCOME_SALES', 'AD1', 'd', 'd'],
        atomic_values: ['na', 'na', '10', '10', '0.50', '3', '0.16', '30', '50', '1', 'x', 'ALL'],
        action_values: [EMPTY_ACTION, EMPTY_ACTION, EMPTY_ACTION, EMPTY_ACTION, EMPTY_ACTION,
          EMPTY_ACTION, EMPTY_ACTION, EMPTY_ACTION, EMPTY_ACTION, EMPTY_ACTION],
        result_values: [{ indicator: 'null' }, { indicator: 'null' },
          { indicator: 'x', value: '2' }, { indicator: 'x' }, { indicator: 'x', value: '0.25' }, { indicator: 'x' }],
      },
    ]),
  );
  const asyncRows = parseAmTabular(
    amBody([
      {
        dimension_values: ['OUTCOME_SALES', 'AD1', 'd', 'd'],
        atomic_values: ['na', 'na', '10', '10', '0.50', '3', '0.16', '30', '50', '1', 'x', 'ALL'],
        action_values: [
          { types: ['lead', 'omni_complete_registration'], values: ['5', '4'], breakdown: 'action_type' },
          EMPTY_ACTION, EMPTY_ACTION, EMPTY_ACTION, EMPTY_ACTION,
          EMPTY_ACTION, EMPTY_ACTION, EMPTY_ACTION, EMPTY_ACTION, EMPTY_ACTION,
        ],
        result_values: [{ indicator: 'null' }, { indicator: 'null' }, { indicator: 'null' },
          { indicator: 'null' }, { indicator: 'null' }, { indicator: 'null' }],
      },
    ]),
  );
  const merged = mergeAmRows([...sync, ...asyncRows]);
  assert.equal(merged.size, 1);
  const r = merged.get('AD1')!;
  assert.equal(r.actions.lead, '5'); // из async
  assert.equal(r.results, '2'); // из sync (async result=null, не затёр)
});

// buildScannedRow: полный маппинг в ScannedAdRow — deposits=results, числа форматируются как DOM.
test('buildScannedRow: маппинг конверсий и метрик', () => {
  const [am] = parseAmTabular(amBody([rowWithConversions()]));
  const row = buildScannedRow(am, {
    adName: 'KE_CR2_a8',
    adsetName: 'adset-1',
    campaignName: 'CR2 | KE | MV | 30.05',
    campaignId: '120203451234560078',
    adsetId: '120203451234560079',
    effectiveStatus: 'ACTIVE',
    budget: '$5',
  });
  assert.equal(row.fb_ad_id, '120244531696570044');
  assert.equal(row.campaign_name, 'CR2 | KE | MV | 30.05');
  // campaign_id прокидывается в каталог (allowlist «Кампании для сканирования»).
  assert.equal(row.campaign_id, '120203451234560078');
  assert.equal(row.adset_id, '120203451234560079');
  assert.equal(row.delivery_status, 'ACTIVE');
  assert.equal(row.spend, '0.01');
  assert.equal(row.impressions, 8);
  assert.equal(row.clicks, 2);
  assert.equal(row.cpc, '0.005');
  assert.equal(row.ctr, '25');
  assert.equal(row.cpm, '1.25');
  assert.equal(row.frequency, '1');
  assert.equal(row.leads, 1);
  assert.equal(row.registrations, 1);
  assert.equal(row.landing_page_views, 1);
  assert.equal(row.outbound_clicks, 2);
  assert.equal(row.deposits, 1); // ← result_values[results,default]
  assert.equal(row.cost_per_result, '0.01');
  assert.equal(row.resolved_offer_code, null);
});

// buildScannedRow: пустая строка → нулевые конверсии, deposits=0, cpc=null.
test('buildScannedRow: пустая строка → нули и null', () => {
  const [am] = parseAmTabular(amBody([rowEmpty()]));
  const row = buildScannedRow(am, { adName: 'x', campaignName: 'c' });
  assert.equal(row.spend, '0.09');
  assert.equal(row.clicks, 0);
  assert.equal(row.cpc, null);
  assert.equal(row.leads, 0);
  assert.equal(row.registrations, 0);
  assert.equal(row.deposits, 0);
  assert.equal(row.cost_per_result, null);
});

// buildScannedRows: батч merged-строк + карта meta.
test('buildScannedRows: батч + meta по ad_id', () => {
  const merged = mergeAmRows(parseAmTabular(amBody([rowWithConversions(), rowEmpty()])));
  const meta = new Map([['120244531696570044', { adName: 'A', campaignName: 'CR2' }]]);
  const rows = buildScannedRows(merged, meta);
  assert.equal(rows.length, 2);
  const first = rows.find((r) => r.fb_ad_id === '120244531696570044')!;
  assert.equal(first.ad_name, 'A');
  const second = rows.find((r) => r.fb_ad_id === '120244530626090044')!;
  assert.equal(second.ad_name, ''); // нет meta → пустое
});

// mapEffectiveStatus: коды FB → стабильные delivery_status (без зависимости от локали).
test('mapEffectiveStatus: коды статусов', () => {
  assert.equal(mapEffectiveStatus('ACTIVE'), 'ACTIVE');
  assert.equal(mapEffectiveStatus('PAUSED'), 'OFF');
  assert.equal(mapEffectiveStatus('ADSET_PAUSED'), 'OFF');
  assert.equal(mapEffectiveStatus('CAMPAIGN_PAUSED'), 'OFF');
  assert.equal(mapEffectiveStatus('ARCHIVED'), 'OFF');
  assert.equal(mapEffectiveStatus('PENDING_REVIEW'), 'IN_REVIEW');
  assert.equal(mapEffectiveStatus('DISAPPROVED'), 'NOT_DELIVERING');
  assert.equal(mapEffectiveStatus('WITH_ISSUES'), 'NOT_DELIVERING');
  assert.equal(mapEffectiveStatus(''), 'UNKNOWN');
  assert.equal(mapEffectiveStatus(undefined), 'UNKNOWN');
});

// parseLightList: id-only (fields=id) и расширенный (name/effective_status).
test('parseLightList: id-only и расширенный', () => {
  const idOnly = parseLightList({ data: [{ id: '111' }, { id: '222' }], paging: {} });
  assert.equal(idOnly.length, 2);
  assert.equal(idOnly[0].id, '111');
  assert.equal(idOnly[0].name, undefined);

  const rich = parseLightList({
    data: [
      {
        id: '111',
        name: 'Camp A',
        effective_status: 'ACTIVE',
        daily_budget: '500',
        campaign_id: '900',
        adset_id: '800',
      },
    ],
  });
  assert.equal(rich[0].name, 'Camp A');
  assert.equal(rich[0].effectiveStatus, 'ACTIVE');
  assert.equal(rich[0].dailyBudget, '500');
  assert.equal(rich[0].campaignId, '900');
  assert.equal(rich[0].adsetId, '800');

  assert.deepEqual(parseLightList(null), []);
  assert.deepEqual(parseLightList({ data: 'x' }), []);
});

// parseLightList: новые поля крео/пиксель/budget_remaining/learning читаются у ад'ов и адсетов.
test('parseLightList: creative thumbnail/image, promoted_object.pixel_id, budget_remaining, learning_stage_info', () => {
  // Сценарий: ответ ads-edge с creative и адсетами с полным набором новых полей.
  const adWithCreative = parseLightList({
    data: [
      {
        id: 'AD1',
        name: 'Ad One',
        effective_status: 'ACTIVE',
        creative: {
          id: 'CR1',
          thumbnail_url: 'https://cdn.fb.com/thumb_160x120.jpg',
          image_url: 'https://cdn.fb.com/full.jpg',
        },
      },
    ],
  });
  assert.equal(adWithCreative[0].creativeThumbUrl, 'https://cdn.fb.com/thumb_160x120.jpg');
  assert.equal(adWithCreative[0].creativeImageUrl, 'https://cdn.fb.com/full.jpg');
  assert.equal(adWithCreative[0].pixelId, undefined); // не задан на ad-edge

  // Сценарий: видео-крео без постера — image_url и object_story_spec отсутствуют → undefined.
  const adVideoCreative = parseLightList({
    data: [{ id: 'AD2', creative: { id: 'CR2', thumbnail_url: 'https://cdn.fb.com/video_thumb.jpg' } }],
  });
  assert.equal(adVideoCreative[0].creativeThumbUrl, 'https://cdn.fb.com/video_thumb.jpg');
  assert.equal(adVideoCreative[0].creativeImageUrl, undefined);

  // Сценарий: видео-крео — top-level image_url пуст, полноразмерный кадр в
  // object_story_spec.video_data.image_url → fallback на постер видео.
  const adVideoPoster = parseLightList({
    data: [
      {
        id: 'AD3',
        creative: {
          id: 'CR3',
          image_url: '',
          object_story_spec: { video_data: { image_url: 'https://cdn.fb.com/poster.jpg' } },
        },
      },
    ],
  });
  assert.equal(adVideoPoster[0].creativeImageUrl, 'https://cdn.fb.com/poster.jpg');

  // Сценарий: top-level image_url задан И есть постер видео → top-level в приоритете.
  const adImagePriority = parseLightList({
    data: [
      {
        id: 'AD4',
        creative: {
          id: 'CR4',
          image_url: 'https://cdn.fb.com/top.jpg',
          object_story_spec: { video_data: { image_url: 'https://cdn.fb.com/poster.jpg' } },
        },
      },
    ],
  });
  assert.equal(adImagePriority[0].creativeImageUrl, 'https://cdn.fb.com/top.jpg');

  // Сценарий: видео-крео без image_url — video_id (top-level и в object_story_spec)
  // извлекается для последующего дотягивания постера из video node.
  const adVideoId = parseLightList({
    data: [{ id: 'AD5', creative: { id: 'CR5', thumbnail_url: 'https://cdn.fb.com/t.jpg', video_id: '777' } }],
  });
  assert.equal(adVideoId[0].videoId, '777');
  assert.equal(adVideoId[0].creativeImageUrl, undefined);

  const adVideoIdNested = parseLightList({
    data: [{ id: 'AD6', creative: { id: 'CR6', object_story_spec: { video_data: { video_id: '888' } } } }],
  });
  assert.equal(adVideoIdNested[0].videoId, '888');

  // Сценарий: ответ adsets-edge с promoted_object.pixel_id, budget_remaining и learning_stage_info.
  const adsetFull = parseLightList({
    data: [
      {
        id: 'AS1',
        name: 'Adset One',
        daily_budget: '100000',
        lifetime_budget: '0',
        budget_remaining: '57300',
        promoted_object: { pixel_id: '987654321' },
        learning_stage_info: { status: 'LEARNING' },
      },
    ],
  });
  assert.equal(adsetFull[0].pixelId, '987654321');
  assert.equal(adsetFull[0].budgetRemaining, '57300');
  assert.equal(adsetFull[0].learningStage, 'LEARNING');
  assert.equal(adsetFull[0].dailyBudget, '100000');
  assert.equal(adsetFull[0].creativeThumbUrl, undefined); // нет у адсета

  // Сценарий: learning_stage_info с LEARNING_LIMITED.
  const adsetLimited = parseLightList({
    data: [{ id: 'AS2', learning_stage_info: { status: 'LEARNING_LIMITED' } }],
  });
  assert.equal(adsetLimited[0].learningStage, 'LEARNING_LIMITED');

  // Сценарий: поля отсутствуют — не паникуем, возвращаем undefined.
  const adsetNoExtra = parseLightList({ data: [{ id: 'AS3', name: 'Plain' }] });
  assert.equal(adsetNoExtra[0].pixelId, undefined);
  assert.equal(adsetNoExtra[0].budgetRemaining, undefined);
  assert.equal(adsetNoExtra[0].learningStage, undefined);
  assert.equal(adsetNoExtra[0].creativeThumbUrl, undefined);
});

// lightNextCursor: курсор пагинации из paging.cursors.after.
test('lightNextCursor: курсор after', () => {
  assert.equal(lightNextCursor({ paging: { cursors: { after: 'CUR123' } } }), 'CUR123');
  assert.equal(lightNextCursor({ paging: {} }), null);
  assert.equal(lightNextCursor({}), null);
});
