import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import type { ScannedAdRow } from '../types.js';
import { findIncompleteScanRowIds } from './am-completeness.js';
import { buildScannedRow } from './am-join.js';
import type { AmRow } from './am-parser.js';

function completeRow(overrides: Partial<ScannedAdRow> = {}): ScannedAdRow {
  return {
    fb_ad_id: '120200000000001',
    campaign_id: '120200000000002',
    adset_id: '120200000000003',
    campaign_name: 'MV | CR2 | KE',
    adset_name: 'KE broad',
    ad_name: 'Creative 1',
    delivery_status: 'ACTIVE',
    moderation_reason: null,
    spend: '1.00',
    budget: '',
    reach: 1,
    impressions: 1,
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
    creative_thumb_url: '',
    creative_image_url: '',
    adset_pixel_id: '',
    adset_daily_budget: '',
    adset_lifetime_budget: '',
    adset_budget_remaining: '',
    adset_learning_stage: '',
    metric_issues: [],
    ...overrides,
  };
}

function rawRow(overrides: Partial<AmRow> = {}): AmRow {
  return {
    adId: '120200000000101',
    objective: null,
    atomic: {
      spend: '1.00',
      reach: '10',
      impressions: '12',
      clicks: '2',
    },
    actions: {},
    costPerAction: {},
    outboundClicks: null,
    outboundCtr: null,
    results: null,
    costPerResult: null,
    ...overrides,
  };
}

const completeMeta = {
  campaignId: '120200000000002',
  adsetId: '120200000000003',
  campaignName: 'MV | CR2 | KE',
  adsetName: 'KE broad',
  adName: 'Creative 1',
  effectiveStatus: 'ACTIVE',
};

describe('findIncompleteScanRowIds', () => {
  it('accepts a complete am_tabular + Graph row', () => {
    assert.deepEqual(findIncompleteScanRowIds([completeRow()]), []);
  });

  it('marks non-canonical ad/campaign/adset IDs and missing hierarchy metadata', () => {
    const rows = [
      completeRow({ fb_ad_id: 'ad-1' }),
      completeRow({ fb_ad_id: '120200000000011', campaign_id: '' }),
      completeRow({ fb_ad_id: '120200000000012', campaign_id: ' 1202 ' }),
      completeRow({ fb_ad_id: '120200000000013', adset_id: '' }),
      completeRow({ fb_ad_id: '120200000000014', campaign_name: ' ' }),
      completeRow({ fb_ad_id: '120200000000015', adset_name: '' }),
      completeRow({ fb_ad_id: '120200000000016', ad_name: '' }),
      completeRow({ fb_ad_id: '120200000000017', delivery_status: 'UNKNOWN' }),
    ];

    assert.deepEqual(findIncompleteScanRowIds(rows), [
      'ad-1',
      '120200000000011',
      '120200000000012',
      '120200000000013',
      '120200000000014',
      '120200000000015',
      '120200000000016',
      '120200000000017',
    ]);
  });

  it('emits a diagnostic marker when the row has no ad ID', () => {
    assert.deepEqual(
      findIncompleteScanRowIds([completeRow({ fb_ad_id: '' })]),
      ['missing_fb_ad_id:row_1'],
    );
  });

  it('marks metadata-complete rows partial when spend or required atomic counters are unknown', () => {
    const rows = [
      buildScannedRow(
        rawRow({
          adId: '120200000000101',
          atomic: { reach: '10', impressions: '12', clicks: '2' },
        }),
        completeMeta,
      ),
      buildScannedRow(
        rawRow({
          adId: '120200000000102',
          atomic: {
            spend: 'broken',
            reach: '10',
            impressions: '12',
            clicks: '2',
          },
        }),
        completeMeta,
      ),
      buildScannedRow(
        rawRow({
          adId: '120200000000103',
          atomic: {
            spend: '1.00',
            reach: '10',
            impressions: '12.5',
            clicks: '2',
          },
        }),
        completeMeta,
      ),
    ];

    assert.deepEqual(findIncompleteScanRowIds(rows), [
      '120200000000101',
      '120200000000102',
      '120200000000103',
    ]);
    assert.deepEqual(rows[0].metric_issues, ['spend:missing']);
    assert.deepEqual(rows[1].metric_issues, ['spend:invalid']);
    assert.deepEqual(rows[2].metric_issues, ['impressions:invalid']);
  });

  it('accepts omitted action slots as zero but rejects populated malformed action counts', () => {
    const confirmedZero = buildScannedRow(rawRow(), completeMeta);
    const malformedAction = buildScannedRow(
      rawRow({
        adId: '120200000000104',
        actions: { lead: 'not-a-count' },
      }),
      completeMeta,
    );

    assert.deepEqual(findIncompleteScanRowIds([confirmedZero]), []);
    assert.equal(confirmedZero.leads, 0);
    assert.deepEqual(findIncompleteScanRowIds([malformedAction]), ['120200000000104']);
    assert.deepEqual(malformedAction.metric_issues, ['leads:invalid']);
  });
});
