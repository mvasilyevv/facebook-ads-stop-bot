/**
 * Хелперы Ads-страницы: парсинг метрик из AdSnapshot, флаги-пороги,
 * извлечение гео из имени, money-форматтер «1 знак» (как в эталоне).
 *
 * Контракт данных (AdSnapshotOut.metrics → MetricsBlock):
 *   spend / ctr / cpm / frequency / cost_per_lead — строки (Decimal → str),
 *   leads / deposits / impressions / clicks / reach — числа.
 *   ROAS в схеме НЕТ → всегда null (рендерим «—», не фейк).
 */

import { deriveGeoFromNames, type AdSnapshot } from "@fb/shared";

// ─── Числовой парс ────────────────────────────────────────────────────────────

/** Безопасный парс строки/числа в number|null. Пустое/NaN → null. */
export function num(v: string | number | null | undefined): number | null {
  if (v == null || v === "") return null;
  const n = typeof v === "string" ? Number.parseFloat(v) : v;
  return Number.isNaN(n) ? null : n;
}

// ─── Мульти-кабинет ──────────────────────────────────────────────────────────

/**
 * Кабинет объявления (числовой ID без act_). Поле приходит с бэка с миграции
 * 0019; в generated-типах появляется после `pnpm gen:api` — до этого мягкий каст.
 */
export function adAccountId(ad: AdSnapshot): string | null {
  return (ad as AdSnapshot & { ad_account_id?: string | null }).ad_account_id ?? null;
}

/**
 * Короткий вид ID кабинета для узких колонок: «…1234» (последние 4 цифры).
 * Арбитражники различают кабинеты по хвосту; полный ID — в title/tooltip.
 */
export function shortAccountId(id: string): string {
  return id.length > 6 ? `…${id.slice(-4)}` : id;
}

/**
 * Deep-link на объявление в Ads Manager (кабинет + selected_ad_ids).
 * null — кабинет неизвестен (legacy-записи каталога) → ссылку не показываем.
 */
export function adsManagerAdUrl(ad: AdSnapshot): string | null {
  const acc = adAccountId(ad);
  if (!acc) return null;
  return `https://adsmanager.facebook.com/adsmanager/manage/ads?act=${acc}&selected_ad_ids=${ad.fb_ad_id}`;
}

// ─── Метрики строки таблицы ─────────────────────────────────────────────────

/** Распарсенные метрики одного объявления (number|null, ROAS всегда null). */
export interface AdMetricsView {
  spend: number | null;
  cpl: number | null;
  freq: number | null;
  cpm: number | null;
  ctr: number | null;
  /** ROAS отсутствует в API-схеме AdSnapshot → всегда null. */
  roas: number | null;
  leads: number | null;
  deposits: number | null;
}

/** Достаёт и парсит метрики из snapshot (или нули-null, если metrics нет). */
export function readAdMetrics(ad: AdSnapshot): AdMetricsView {
  const m = ad.metrics;
  return {
    spend: num(m?.spend),
    cpl: num(m?.cost_per_lead),
    freq: num(m?.frequency),
    cpm: num(m?.cpm),
    ctr: num(m?.ctr),
    roas: null, // нет в схеме
    leads: m?.leads ?? null,
    deposits: m?.deposits ?? null,
  };
}

// ─── Пороги-флаги (канон ads-web.jsx) ──────────────────────────────────────

/** CPL > 30 → danger. */
export const isCplBad = (v: number | null): boolean => v != null && v > 30;
/** FREQ > 4 → danger. */
export const isFreqBad = (v: number | null): boolean => v != null && v > 4;
/** ROAS < 1 → danger (только если значение есть). */
export const isRoasBad = (v: number | null): boolean => v != null && v < 1;

// ─── Money-форматтер «1 знак» (как в прототипе) ────────────────────────────

const MONEY1 = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
});

/** $1,234.5 — формат денег с одним знаком (эталонный money() из ads-web.jsx). */
export function money1(v: number | null | undefined): string {
  if (v == null) return "—";
  return "$" + MONEY1.format(v);
}

// ─── Гео из имени объявления ───────────────────────────────────────────────

/**
 * Гео-плейсхолдер для thumb. Реализация сведена в @fb/shared (deriveGeoFromNames) —
 * единый алгоритм с mini (дедуп, аудит 2026-06-09). Обёртка сохраняет прежнюю
 * сигнатуру по AdSnapshot для всех call-sites web.
 */
export function deriveGeo(ad: Pick<AdSnapshot, "ad_name" | "campaign_name">): string {
  return deriveGeoFromNames(ad.campaign_name, ad.ad_name);
}
