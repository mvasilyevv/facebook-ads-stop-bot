/**
 * Shared number formatting.
 *
 * Money always requires an explicit server-confirmed currency. The repository
 * contract owns its exponent (JPY 0, USD 2, KWD 3); the client never assumes
 * two decimals or invents USD for an unknown amount.
 */

import { supportedCurrencyExponent } from "./currencyContract";

export {
  SUPPORTED_CURRENCY_EXPONENTS,
  supportedCurrencyExponent,
} from "./currencyContract";

const MONEY_FORMATTERS = new Map<string, Intl.NumberFormat>();
const DECIMAL = /^-?\d+(?:\.\d+)?$/;

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

/** Whether the repository has a reviewed exponent for this currency. */
export function isSupportedCurrencyCode(
  value: unknown,
): value is string {
  return supportedCurrencyExponent(value) !== null;
}

/** Format a major-unit decimal with its explicit currency code. */
export function formatSpend(
  value: number | string | null | undefined,
  currency: string | null | undefined,
): string {
  if (value == null || value === "") return "—";
  const normalizedCurrency = currency?.trim().toUpperCase() ?? "";
  const exponent = supportedCurrencyExponent(normalizedCurrency);
  if (exponent === null) return "—";
  if (typeof value === "string" && !DECIMAL.test(value)) return "—";
  if (typeof value === "number" && !Number.isFinite(value)) return "—";
  try {
    let formatter = MONEY_FORMATTERS.get(normalizedCurrency);
    if (!formatter) {
      formatter = new Intl.NumberFormat("en-US", {
        style: "currency",
        currency: normalizedCurrency,
        currencyDisplay: "code",
        minimumFractionDigits: exponent,
        maximumFractionDigits: exponent,
      });
      MONEY_FORMATTERS.set(normalizedCurrency, formatter);
    }
    return formatExactNumber(formatter, value);
  } catch {
    return "—";
  }
}

/**
 * Format an exact per-unit amount derived from a decimal total and count.
 *
 * The quotient is rounded half-away-from-zero at the reviewed currency
 * exponent using BigInt arithmetic, so funnel costs never pass through Number.
 */
export function formatSpendPerUnit(
  total: string | null | undefined,
  count: number | null | undefined,
  currency: string | null | undefined,
): string {
  if (
    total == null ||
    !DECIMAL.test(total) ||
    !Number.isSafeInteger(count) ||
    Number(count) <= 0
  ) {
    return "—";
  }
  const normalizedCurrency = currency?.trim().toUpperCase() ?? "";
  const exponent = supportedCurrencyExponent(normalizedCurrency);
  if (exponent === null) return "—";
  const perUnit = divideDecimalToScale(total, Number(count), exponent);
  return perUnit === null ? "—" : formatSpend(perUnit, normalizedCurrency);
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

function formatExactNumber(
  formatter: Intl.NumberFormat,
  value: number | string,
): string {
  // ECMA-402 parses decimal strings as mathematical values instead of first
  // coercing them to an IEEE-754 Number. TypeScript's ES2022 lib still exposes
  // the older number|bigint signature, hence the narrow call-site cast.
  const format = formatter.format as unknown as (
    candidate: number | string,
  ) => string;
  return format(value);
}

function divideDecimalToScale(
  value: string,
  divisor: number,
  targetScale: number,
): string | null {
  const match = /^(-?)(\d+)(?:\.(\d+))?$/.exec(value);
  if (!match) return null;
  const negative = match[1] === "-";
  const integerDigits = match[2];
  if (integerDigits === undefined) return null;
  const fractionDigits = match[3] ?? "";
  const numerator = BigInt(`${integerDigits}${fractionDigits}`);
  const sourceScale = 10n ** BigInt(fractionDigits.length);
  const denominator = BigInt(divisor) * sourceScale;
  const scaledNumerator = numerator * 10n ** BigInt(targetScale);
  let quotient = scaledNumerator / denominator;
  const remainder = scaledNumerator % denominator;
  if (remainder * 2n >= denominator) quotient += 1n;
  if (negative && quotient !== 0n) quotient = -quotient;
  return scaledIntegerToDecimal(quotient, targetScale);
}

function scaledIntegerToDecimal(value: bigint, scale: number): string {
  const negative = value < 0n;
  const absoluteDigits = (negative ? -value : value).toString();
  if (scale === 0) return `${negative ? "-" : ""}${absoluteDigits}`;
  const padded = absoluteDigits.padStart(scale + 1, "0");
  const whole = padded.slice(0, -scale);
  const fraction = padded.slice(-scale);
  return `${negative ? "-" : ""}${whole}.${fraction}`;
}
