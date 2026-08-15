// Джойн метрик am_tabular с метаданными light_* -> ScannedAdRow (тот же контракт, что DOM-парсер).
// Переиспользуем парс-хелперы parser.ts → значения форматируются БАЙТ-В-БАЙТ как в DOM-пути.
// Маппинг конверсий money-критичен (см. docs/am_tabular_scanner_plan.md §3).

import type { ScannedAdRow } from '../types.js';
import type { AmRow } from './am-parser.js';

// am_tabular отдаёт УЖЕ машинный формат (точка-десятичная, без разделителей тысяч),
// поэтому НЕ переиспользуем locale-эвристику parser.ts (она ломает "0.005"→"0005").
// Значения только валидируем и прокидываем как есть.
const AM_DECIMAL_RE = /^\d+(?:\.\d+)?$/;
const AM_COUNT_RE = /^\d+$/;
const PROTO_INT32_MAX = 2_147_483_647;

// Числовая строка как есть, либо null (для опциональных Decimal-полей).
function amNum(v: string | null | undefined): string | null {
  if (v === null || v === undefined) return null;
  const s = String(v).trim();
  return AM_DECIMAL_RE.test(s) ? s : null;
}

function isAmCount(v: string | null | undefined): boolean {
  if (v === null || v === undefined) return false;
  const s = String(v).trim();
  if (!AM_COUNT_RE.test(s)) return false;
  const parsed = Number(s);
  return Number.isSafeInteger(parsed) && parsed <= PROTO_INT32_MAX;
}

// Целое (reach/impressions/clicks/leads/...). Невалидное значение получает
// protobuf placeholder 0, но metric_issues делает строку partial до сериализации.
function amInt(v: string | null | undefined): number {
  if (!isAmCount(v)) return 0;
  return Number(String(v).trim());
}

// Текущий protobuf не допускает null. "0" здесь — только transport placeholder:
// отсутствующий/невалидный spend обязательно попадает в metric_issues, а scan gate
// не пропускает такую строку в observer writers/FSM.
function amMoney(v: string | null | undefined): string {
  return amNum(v) ?? '0';
}

function metricIssues(am: AmRow, lpvK: string): string[] {
  const issues: string[] = [];
  const requiredDecimal = (field: string, value: string | null | undefined): void => {
    if (value === null || value === undefined) {
      issues.push(`${field}:missing`);
    } else if (amNum(value) === null) {
      issues.push(`${field}:invalid`);
    }
  };
  const requiredCount = (field: string, value: string | null | undefined): void => {
    if (value === null || value === undefined) {
      issues.push(`${field}:missing`);
    } else if (!isAmCount(value)) {
      issues.push(`${field}:invalid`);
    }
  };
  const optionalDecimal = (field: string, value: string | null | undefined): void => {
    if (value !== null && value !== undefined && amNum(value) === null) {
      issues.push(`${field}:invalid`);
    }
  };
  const optionalCount = (field: string, value: string | null | undefined): void => {
    // Meta represents a confirmed zero action by omitting that action type
    // from an otherwise present action slot. Only a populated invalid value is
    // incomplete; omission remains the confirmed-zero representation.
    if (value !== null && value !== undefined && !isAmCount(value)) {
      issues.push(`${field}:invalid`);
    }
  };

  requiredDecimal('spend', am.atomic['spend']);
  requiredCount('reach', am.atomic['reach']);
  requiredCount('impressions', am.atomic['impressions']);
  requiredCount('clicks', am.atomic['clicks']);

  optionalDecimal('cpc', am.atomic['cpc']);
  optionalDecimal('ctr', am.atomic['ctr']);
  optionalDecimal('cpm', am.atomic['cpm']);
  optionalDecimal('frequency', am.atomic['frequency']);
  optionalDecimal('outbound_ctr', am.outboundCtr);
  optionalDecimal('cost_per_landing_page_view', am.costPerAction[lpvK]);
  optionalDecimal('cost_per_result', am.costPerResult);
  optionalDecimal('cost_per_lead', am.costPerAction[LEAD_TYPE]);
  optionalDecimal(
    'cost_per_registration',
    am.costPerAction[REGISTRATION_TYPE],
  );

  optionalCount('outbound_clicks', am.outboundClicks);
  optionalCount('landing_page_views', am.actions[lpvK]);
  optionalCount('leads', am.actions[LEAD_TYPE]);
  optionalCount('registrations', am.actions[REGISTRATION_TYPE]);
  optionalCount('deposits', am.results);

  return issues;
}

// Метаданные объявления, собранные из light_* (имена/статус/бюджет/крео/адсет).
export interface AmAdMeta {
  adName?: string;
  adsetName?: string;
  campaignName?: string;
  campaignId?: string;
  adsetId?: string;
  effectiveStatus?: string;
  moderationReason?: string;
  budget?: string;
  creativeThumbUrl?: string;
  creativeImageUrl?: string;
  pixelId?: string;
  dailyBudget?: string;
  lifetimeBudget?: string;
  budgetRemaining?: string;
  learningStage?: string;
}

// effective_status (light_*) сохраняется отдельными кодами Meta. Это не зависит
// от локали профиля Vision и не скрывает DISAPPROVED внутри NOT_DELIVERING.
export function mapEffectiveStatus(status: string | undefined): string {
  const s = (status || '').trim().toUpperCase();
  return s || 'UNKNOWN';
}

// Конверсии: какие action_type считаем лидами/регами/LPV
// (лид→lead, регистрац→omni_complete_registration, целев→landing_page_view).
const LEAD_TYPE = 'lead';
const REGISTRATION_TYPE = 'omni_complete_registration';
const LPV_TYPE = 'landing_page_view';
const OMNI_LPV_TYPE = 'omni_landing_page_view';

// LPV: Meta всё чаще отдаёт unified/omni-метрики. Фильтр (AM_ACTION_TYPES) запрашивает
// ОБЕ формы; предпочитаем omni, fallback на non-omni — иначе при LPV под
// omni_landing_page_view поле было бы 0 (BA-6). Count и cost берём из ОДНОГО ключа,
// чтобы не рассинхронить (не суммируем — иначе двойной счёт).
function lpvKey(actions: Record<string, string>): string {
  return actions[OMNI_LPV_TYPE] !== undefined ? OMNI_LPV_TYPE : LPV_TYPE;
}

// Одна merged-строка am_tabular + meta -> ScannedAdRow.
export function buildScannedRow(am: AmRow, meta: AmAdMeta = {}): ScannedAdRow {
  const a = am.atomic;
  const lpvK = lpvKey(am.actions); // omni LPV предпочтительнее non-omni (BA-6)
  const issues = metricIssues(am, lpvK);
  return {
    fb_ad_id: am.adId,
    campaign_id: meta.campaignId ?? '',
    adset_id: meta.adsetId ?? '',
    campaign_name: meta.campaignName ?? '',
    adset_name: meta.adsetName ?? '',
    ad_name: meta.adName ?? '',
    delivery_status: mapEffectiveStatus(meta.effectiveStatus),
    moderation_reason: meta.moderationReason ?? null,
    spend: amMoney(a['spend']),
    budget: meta.budget ?? '',
    reach: amInt(a['reach']),
    impressions: amInt(a['impressions']),
    clicks: amInt(a['clicks']),
    cpc: amNum(a['cpc']),
    ctr: amNum(a['ctr']),
    outbound_clicks: amInt(am.outboundClicks),
    outbound_ctr: amNum(am.outboundCtr),
    landing_page_views: amInt(am.actions[lpvK]),
    cost_per_landing_page_view: amNum(am.costPerAction[lpvK]),
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
    creative_thumb_url: meta.creativeThumbUrl ?? '',
    creative_image_url: meta.creativeImageUrl ?? '',
    adset_pixel_id: meta.pixelId ?? '',
    adset_daily_budget: meta.dailyBudget ?? '',
    adset_lifetime_budget: meta.lifetimeBudget ?? '',
    adset_budget_remaining: meta.budgetRemaining ?? '',
    adset_learning_stage: meta.learningStage ?? '',
    metric_issues: issues,
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
