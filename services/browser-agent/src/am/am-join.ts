// Джойн метрик am_tabular с метаданными light_* -> ScannedAdRow (тот же контракт, что DOM-парсер).
// Переиспользуем парс-хелперы parser.ts → значения форматируются БАЙТ-В-БАЙТ как в DOM-пути.
// Маппинг конверсий money-критичен (см. docs/am_tabular_scanner_plan.md §3).

import type { ScannedAdRow } from '../types.js';
import type { AmRow } from './am-parser.js';

// am_tabular отдаёт УЖЕ машинный формат (точка-десятичная, без разделителей тысяч),
// поэтому НЕ переиспользуем locale-эвристику parser.ts (она ломает "0.005"→"0005").
// Значения только валидируем и прокидываем как есть.
const NUM_RE = /^-?\d+(\.\d+)?$/;

// Числовая строка как есть, либо null (для опциональных Decimal-полей).
function amNum(v: string | null | undefined): string | null {
  if (v === null || v === undefined) return null;
  const s = String(v).trim();
  return NUM_RE.test(s) ? s : null;
}

// Целое (reach/impressions/clicks/leads/...). Нечисло/пусто → 0.
function amInt(v: string | null | undefined): number {
  const s = amNum(v);
  if (s === null) return 0;
  return Math.trunc(Number(s)) || 0;
}

// Деньги-строка с дефолтом "0" (spend всегда заполнен, как в DOM-пути).
function amMoney(v: string | null | undefined): string {
  return amNum(v) ?? '0';
}

// Метаданные объявления, собранные из light_* (имена/статус/бюджет).
export interface AmAdMeta {
  adName?: string;
  adsetName?: string;
  campaignName?: string;
  campaignId?: string;
  effectiveStatus?: string;
  budget?: string;
}

// effective_status (light_*) -> те же коды, что detectDeliveryStatus в DOM-пути.
// Не зависит от локали профиля Vision — статус берём из данных, а не из текста.
export function mapEffectiveStatus(status: string | undefined): string {
  const s = (status || '').trim().toUpperCase();
  if (!s) return 'UNKNOWN';
  switch (s) {
    case 'ACTIVE':
      return 'ACTIVE';
    case 'PAUSED':
    case 'ADSET_PAUSED':
    case 'CAMPAIGN_PAUSED':
    case 'CAMPAIGN_GROUP_PAUSED':
    case 'ARCHIVED':
    case 'DELETED':
      return 'OFF';
    case 'IN_PROCESS':
    case 'PROCESSING':
      return 'PROCESSING';
    case 'PENDING_REVIEW':
    case 'PREAPPROVED':
      return 'IN_REVIEW';
    case 'DISAPPROVED':
    case 'WITH_ISSUES':
    case 'PENDING_BILLING_INFO':
    case 'ADSET_PAUSED_NOT_DELIVERING':
      return 'NOT_DELIVERING';
    default:
      return s;
  }
}

// Конверсии: какие action_type считаем лидами/регами/LPV
// (лид→lead, регистрац→omni_complete_registration, целев→landing_page_view).
const LEAD_TYPE = 'lead';
const REGISTRATION_TYPE = 'omni_complete_registration';
const LPV_TYPE = 'landing_page_view';

// Одна merged-строка am_tabular + meta -> ScannedAdRow.
export function buildScannedRow(am: AmRow, meta: AmAdMeta = {}): ScannedAdRow {
  const a = am.atomic;
  return {
    fb_ad_id: am.adId,
    campaign_id: meta.campaignId ?? '',
    campaign_name: meta.campaignName ?? '',
    adset_name: meta.adsetName ?? '',
    ad_name: meta.adName ?? '',
    delivery_status: mapEffectiveStatus(meta.effectiveStatus),
    spend: amMoney(a['spend']),
    budget: meta.budget ?? '',
    reach: amInt(a['reach']),
    impressions: amInt(a['impressions']),
    clicks: amInt(a['clicks']),
    cpc: amNum(a['cpc']),
    ctr: amNum(a['ctr']),
    outbound_clicks: amInt(am.outboundClicks),
    outbound_ctr: amNum(am.outboundCtr),
    landing_page_views: amInt(am.actions[LPV_TYPE]),
    cost_per_landing_page_view: amNum(am.costPerAction[LPV_TYPE]),
    cost_per_result: amNum(am.costPerResult),
    cpm: amNum(a['cpm']),
    frequency: amNum(a['frequency']),
    leads: amInt(am.actions[LEAD_TYPE]),
    cost_per_lead: amNum(am.costPerAction[LEAD_TYPE]),
    registrations: amInt(am.actions[REGISTRATION_TYPE]),
    cost_per_registration: amNum(am.costPerAction[REGISTRATION_TYPE]),
    // deposits ← Meta "results" (как DOM «Результат»→deposits). Депозиты для ПРАВИЛ —
    // отдельный источник AdSet.pro (external_deposits в pipeline), здесь не трогаем.
    deposits: amInt(am.results),
    resolved_offer_code: null,
  };
}

// Собрать ScannedAdRow по всем merged am-строкам + карты meta из light_*.
// adId -> ad-meta; adsetMeta/campaignMeta резолвятся по иерархии при наличии (Ф2).
export function buildScannedRows(
  merged: Map<string, AmRow>,
  adMeta: Map<string, AmAdMeta>,
): ScannedAdRow[] {
  const out: ScannedAdRow[] = [];
  for (const [adId, am] of merged) {
    out.push(buildScannedRow(am, adMeta.get(adId) ?? {}));
  }
  return out;
}
