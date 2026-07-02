/**
 * Числовые форматтеры. Синглтоны Intl — создаются один раз, дёшево.
 * Все функции безопасны к null/undefined → возвращают "—".
 *
 * Портировано из frontend/src/lib/utils/format.ts (эталон).
 */

const SPEND_FORMATTER = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

/** Один знак после запятой — компактный money-формат для плотных таблиц/панелей. */
const SPEND1_FORMATTER = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
});

const COMPACT_FORMATTER = new Intl.NumberFormat("en-US", {
  notation: "compact",
  maximumFractionDigits: 1,
});

const PERCENT_FORMATTER = new Intl.NumberFormat("en-US", {
  style: "percent",
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
});

const INT_FORMATTER = new Intl.NumberFormat("en-US");

/**
 * Денежная сумма: $1,234.56.
 * Принимает число или строку (бэк отдаёт spend как строку из Decimal).
 */
export function formatSpend(value: number | string | null | undefined): string {
  if (value == null || value === "") return "—";
  const n = typeof value === "string" ? Number.parseFloat(value) : value;
  if (Number.isNaN(n)) return "—";
  return SPEND_FORMATTER.format(n);
}

/**
 * Денежная сумма с ОДНИМ знаком: $1,234.5 — компактный вариант formatSpend()
 * для плотных таблиц/панелей (Ads-таблица, метрики-панель объявления).
 * Единый источник — раньше был локально продублирован в web (money1 в
 * adHelpers.ts), сведено сюда во избежание рассинхрона форматов между
 * web и mini (аудит 02.07, LOW F1).
 */
export function formatSpend1(value: number | string | null | undefined): string {
  if (value == null || value === "") return "—";
  const n = typeof value === "string" ? Number.parseFloat(value) : value;
  if (Number.isNaN(n)) return "—";
  return "$" + SPEND1_FORMATTER.format(n);
}

/** Компактное число: 12.4K, 1.2M. */
export function formatCompact(value: number | null | undefined): string {
  if (value == null) return "—";
  return COMPACT_FORMATTER.format(value);
}

/** Целое с разрядными разделителями: 1,234. */
export function formatInt(value: number | null | undefined): string {
  if (value == null) return "—";
  return INT_FORMATTER.format(value);
}

/**
 * Процент из дроби 0..1: 0.124 → "12.4%".
 * Используй для rate/ratio метрик (CTR, конверсия).
 */
export function formatPercent(value: number | null | undefined): string {
  if (value == null) return "—";
  return PERCENT_FORMATTER.format(value);
}

/**
 * Процент из числа уже в процентах: 12.4 → "12.4%".
 * Используй для roi_percent, frequency_percent и подобных полей.
 */
export function formatPercentValue(value: number | string | null | undefined): string {
  if (value == null || value === "") return "—";
  const n = typeof value === "string" ? Number.parseFloat(value) : value;
  if (Number.isNaN(n)) return "—";
  return `${n.toFixed(1)}%`;
}
