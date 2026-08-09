export function browserTimeZone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
}

export function isValidTimeZone(value: string): boolean {
  try {
    new Intl.DateTimeFormat("ru-RU", { timeZone: value }).format(new Date());
    return true;
  } catch {
    return false;
  }
}

function dateValue(value: string | Date | null | undefined): Date | null {
  if (!value) return null;
  const date = value instanceof Date ? value : new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function formatDisplayTime(
  value: string | Date | null | undefined,
  options: Intl.DateTimeFormatOptions,
  timeZone: string,
): string {
  const date = dateValue(value);
  if (!date) return "—";
  try {
    return new Intl.DateTimeFormat("ru-RU", {
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
      ...options,
      timeZone,
    }).format(date);
  } catch {
    return "—";
  }
}

export function formatDisplayDateTime(
  value: string | Date | null | undefined,
  timeZone: string,
): string {
  return formatDisplayTime(
    value,
    { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" },
    timeZone,
  );
}

export function formatDisplayDate(
  value: string | Date | null | undefined,
  timeZone: string,
): string {
  return formatDisplayTime(value, { day: "2-digit", month: "short", year: "numeric" }, timeZone);
}
