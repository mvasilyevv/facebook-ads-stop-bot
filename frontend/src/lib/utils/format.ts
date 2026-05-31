/**
 * Форматтеры — числа, валюта, дата, ID объявлений.
 * Все возвращают строку, безопасны к null/undefined.
 */

const SPEND_FORMATTER = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
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

/** Денежная сумма: $1,234.56. Null/undefined → "—". */
export function formatSpend(value: number | string | null | undefined): string {
  if (value == null || value === "") return "—";
  const n = typeof value === "string" ? Number.parseFloat(value) : value;
  if (Number.isNaN(n)) return "—";
  return SPEND_FORMATTER.format(n);
}

/** Компактное число: 12.4K, 1.2M. */
export function formatCompact(value: number | null | undefined): string {
  if (value == null) return "—";
  return COMPACT_FORMATTER.format(value);
}

/** Целое: 1,234. */
export function formatInt(value: number | null | undefined): string {
  if (value == null) return "—";
  return INT_FORMATTER.format(value);
}

/** Процент из дроби: 0.124 → "12.4%". */
export function formatPercent(value: number | null | undefined): string {
  if (value == null) return "—";
  return PERCENT_FORMATTER.format(value);
}

/** Процент из числа уже в процентах: 12.4 → "12.4%". */
export function formatPercentValue(value: number | string | null | undefined): string {
  if (value == null || value === "") return "—";
  const n = typeof value === "string" ? Number.parseFloat(value) : value;
  if (Number.isNaN(n)) return "—";
  return `${n.toFixed(1)}%`;
}

/** Сокращение длинного Meta-ID: "120211...8761". */
export function truncateAdId(adId: string | null | undefined, headLen = 6, tailLen = 4): string {
  if (!adId) return "—";
  if (adId.length <= headLen + tailLen + 3) return adId;
  return `${adId.slice(0, headLen)}...${adId.slice(-tailLen)}`;
}

/** Относительное время (возраст), компактно по-русски: "5 мин", "2 ч", "3 дн". */
export function formatRelativeTime(iso: string | Date | null | undefined): string {
  if (!iso) return "—";
  const date = typeof iso === "string" ? new Date(iso) : iso;
  if (Number.isNaN(date.getTime())) return "—";
  const abs = Math.abs(Math.round((Date.now() - date.getTime()) / 1000));
  if (abs < 45) return "сейчас";
  if (abs < 3600) return `${Math.round(abs / 60)} мин`;
  if (abs < 86400) return `${Math.round(abs / 3600)} ч`;
  if (abs < 2592000) return `${Math.round(abs / 86400)} дн`;
  return formatDateTime(date);
}

/** Time-of-day в UTC: "14:32:18". Бэкенд хранит всё в UTC — показываем без локального сдвига. */
export function formatTimeOfDay(iso: string | Date | null | undefined): string {
  if (!iso) return "—";
  const date = typeof iso === "string" ? new Date(iso) : iso;
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleTimeString("en-GB", { hour12: false, timeZone: "UTC" });
}

/** Полная дата: "2026-05-28 14:32". */
export function formatDateTime(iso: string | Date | null | undefined): string {
  if (!iso) return "—";
  const date = typeof iso === "string" ? new Date(iso) : iso;
  if (Number.isNaN(date.getTime())) return "—";
  return date.toISOString().slice(0, 16).replace("T", " ");
}

/** Длительность в секундах: "12m", "2h 4m", "3d". */
export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null || seconds < 0) return "—";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  if (seconds < 86400) {
    const h = Math.floor(seconds / 3600);
    const m = Math.round((seconds % 3600) / 60);
    return m > 0 ? `${h}h ${m}m` : `${h}h`;
  }
  return `${Math.round(seconds / 86400)}d`;
}
