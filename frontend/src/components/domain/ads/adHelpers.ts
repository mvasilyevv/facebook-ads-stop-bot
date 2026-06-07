/**
 * Хелперы Ads-страницы: парсинг метрик из AdSnapshot, флаги-пороги,
 * извлечение гео из имени, money-форматтер «1 знак» (как в эталоне).
 *
 * Контракт данных (AdSnapshotOut.metrics → MetricsBlock):
 *   spend / ctr / cpm / frequency / cost_per_lead — строки (Decimal → str),
 *   leads / deposits / impressions / clicks / reach — числа.
 *   ROAS в схеме НЕТ → всегда null (рендерим «—», не фейк).
 */

import type { AdSnapshot } from "@fb/shared";

// ─── Числовой парс ────────────────────────────────────────────────────────────

/** Безопасный парс строки/числа в number|null. Пустое/NaN → null. */
export function num(v: string | number | null | undefined): number | null {
  if (v == null || v === "") return null;
  const n = typeof v === "string" ? Number.parseFloat(v) : v;
  return Number.isNaN(n) ? null : n;
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

// ISO-2 коды, реально встречающиеся в трафике (расширяемо). Регистр верхний.
const KNOWN_GEOS = new Set([
  "PT", "BR", "UA", "DE", "IT", "ES", "FR", "NL", "PL", "GB", "GH", "NG",
  "US", "CA", "AU", "MX", "AR", "CL", "CO", "PE", "RO", "CZ", "GR", "TR",
  "IN", "ID", "PH", "TH", "VN", "ZA", "KE", "EG", "MA", "SA", "AE", "KZ",
]);

/**
 * Извлекает 2-буквенное гео из имени объявления/кампании.
 * Стратегия: ищем токен (split по разделителям), который является известным
 * ISO-2 кодом ИЛИ начинается с пары заглавных, совпадающей с известным гео
 * (напр. «GH12» → GH). Фолбэк — первые 2 буквы первого токена в upper.
 * Гео-плейсхолдер для thumb; настоящие креативы заменят его позже.
 */
export function deriveGeo(ad: Pick<AdSnapshot, "ad_name" | "campaign_name">): string {
  const source = `${ad.campaign_name ?? ""} ${ad.ad_name ?? ""}`;
  const tokens = source.split(/[\s|/_\-.,]+/).filter(Boolean);
  for (const t of tokens) {
    const up = t.toUpperCase();
    if (KNOWN_GEOS.has(up)) return up;
    const head = up.slice(0, 2);
    // «GH12», «UA7» — гео-код с приклеенным числом.
    if (/^[A-Z]{2}\d/.test(up) && KNOWN_GEOS.has(head)) return head;
  }
  // Фолбэк: первые две буквы первого алфавитного токена.
  const firstWord = tokens.find((t) => /[a-zA-Z]/.test(t));
  if (firstWord) {
    const letters = firstWord.replace(/[^a-zA-Z]/g, "").slice(0, 2).toUpperCase();
    if (letters.length === 2) return letters;
  }
  return "—";
}
