import type { ScannedAdRow } from '../types.js';

const NUMERIC_META_ID = /^\d+$/;

// Wire-level proof that this producer applies the fail-closed metric
// completeness semantics implemented below. Increment only for an incompatible
// semantic revision, never for ordinary implementation changes.
export const METRICS_CONTRACT_REVISION = 1;

function hasRequiredText(value: string): boolean {
  return typeof value === 'string' && value.trim().length > 0;
}

function hasRequiredDeliveryStatus(value: string): boolean {
  return hasRequiredText(value) && value.trim().toUpperCase() !== 'UNKNOWN';
}

function hasValidRequiredMoney(value: string): boolean {
  return typeof value === 'string' && /^\d+(?:\.\d+)?$/.test(value.trim());
}

function hasValidRequiredCount(value: number): boolean {
  return Number.isSafeInteger(value) && value >= 0 && value <= 2_147_483_647;
}

function hasCompleteMetricProvenance(row: ScannedAdRow): boolean {
  return (
    Array.isArray(row.metric_issues)
    && row.metric_issues.length === 0
    && hasValidRequiredMoney(row.spend)
    && hasValidRequiredCount(row.reach)
    && hasValidRequiredCount(row.impressions)
    && hasValidRequiredCount(row.clicks)
  );
}

function partialRowMarker(row: ScannedAdRow, index: number): string {
  const adId = typeof row.fb_ad_id === 'string' ? row.fb_ad_id.trim() : '';
  return adId || `missing_fb_ad_id:row_${index + 1}`;
}

/**
 * Return diagnostic markers for rows that cannot safely identify a Meta ad
 * and its catalog hierarchy, or whose required money evidence is incomplete.
 *
 * Meta object IDs are canonical digit-only strings. Names and delivery status
 * come from the Graph metadata edges; UNKNOWN is the joiner's sentinel for a
 * missing effective_status and is therefore incomplete, not a real status.
 */
export function findIncompleteScanRowIds(rows: readonly ScannedAdRow[]): string[] {
  const partial = new Set<string>();
  rows.forEach((row, index) => {
    const incomplete =
      !NUMERIC_META_ID.test(row.fb_ad_id) ||
      !NUMERIC_META_ID.test(row.campaign_id) ||
      !NUMERIC_META_ID.test(row.adset_id) ||
      !hasRequiredText(row.campaign_name) ||
      !hasRequiredText(row.adset_name) ||
      !hasRequiredText(row.ad_name) ||
      !hasRequiredDeliveryStatus(row.delivery_status) ||
      !hasCompleteMetricProvenance(row);
    if (incomplete) {
      partial.add(partialRowMarker(row, index));
    }
  });
  return [...partial];
}
