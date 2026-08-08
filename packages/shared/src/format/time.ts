/**
 * Временные форматтеры. UTC используется по умолчанию; кабинетные значения
 * форматируются только с явно переданным IANA timezone.
 * Все функции безопасны к null/undefined/невалидным ISO → "—".
 *
 * Портировано из frontend/src/lib/utils/format.ts (эталон).
 */

/**
 * Относительное время (возраст метки) по-русски: "сейчас", "5 мин", "2 ч", "3 дн".
 * При очень старых датах (>30 дней) переходит в formatDateTime.
 */
export function formatRelativeTime(
  iso: string | Date | null | undefined,
): string {
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

/**
 * Время суток в UTC: "14:32:18".
 * Используется для меток событий (scan_runs, alert_events).
 */
export function formatTimeOfDay(iso: string | Date | null | undefined): string {
  if (!iso) return "—";
  const date = typeof iso === "string" ? new Date(iso) : iso;
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleTimeString("en-GB", { hour12: false, timeZone: "UTC" });
}

/**
 * Полная дата+время UTC: "2026-05-28 14:32".
 * Используется для created_at, updated_at, длинных периодов.
 */
export function formatDateTime(iso: string | Date | null | undefined): string {
  if (!iso) return "—";
  const date = typeof iso === "string" ? new Date(iso) : iso;
  if (Number.isNaN(date.getTime())) return "—";
  return date.toISOString().slice(0, 16).replace("T", " ");
}

/**
 * Timestamp in the server-declared IANA timezone. Operator surfaces must use
 * this instead of the browser timezone: the cabinet day is authoritative.
 */
export function formatZonedDateTime(
  iso: string | Date | null | undefined,
  timeZone: string,
): string {
  const date = parseDate(iso);
  if (!date) return "—";
  try {
    return new Intl.DateTimeFormat("ru-RU", {
      timeZone,
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).format(date);
  } catch {
    return "—";
  }
}

/** Time-of-day in the server-declared IANA timezone. */
export function formatZonedTime(
  iso: string | Date | null | undefined,
  timeZone: string,
): string {
  const date = parseDate(iso);
  if (!date) return "—";
  try {
    return new Intl.DateTimeFormat("ru-RU", {
      timeZone,
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).format(date);
  } catch {
    return "—";
  }
}

export type TimezoneEvidenceState = "single" | "mixed" | "unknown";

/** Human-readable evidence label that never turns mixed/unknown into UTC. */
export function timezoneEvidenceLabel(
  timeZone: string | null | undefined,
  state: TimezoneEvidenceState,
): string {
  if (state === "single" && timeZone) return timeZone;
  if (state === "mixed") {
    return "Несколько часовых поясов · границы по каждому кабинету";
  }
  return "Не подтверждён";
}

function parseDate(iso: string | Date | null | undefined): Date | null {
  if (!iso) return null;
  const date = typeof iso === "string" ? new Date(iso) : iso;
  return Number.isNaN(date.getTime()) ? null : date;
}

/**
 * Длительность в секундах → компактная строка.
 * <60s → "42s", <3600 → "12m", <86400 → "2h 4m", иначе → "3d".
 * Используется для incident_duration_seconds, scan duration.
 */
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
